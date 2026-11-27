"""Сохранённые поиски, жалобы и публичная конфигурация мини-приложения."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Ctx, get_admin_user, get_current_user, get_registered
from api.shops import _notify_admins
from database import get_session
from models import Category, MarketplaceItem, Report, SavedSearch, Shop, User
from schemas import ReportIn, SavedSearchIn, SavedSearchOut
from config import MINIAPP_SHORT_NAME
from services.notify_service import get_bot_username
from services.rate_limit import client_key, rate_limit
from services.telegram_service import bot
from services.text import clean_str

router = APIRouter(tags=["extras"])
admin_router = APIRouter(prefix="/api/admin/reports", tags=["admin"])

MAX_SAVED_SEARCHES = 10
REPORT_REASONS = ("spam", "fraud", "prohibited", "wrong_category", "other")
REPORT_LABELS = {
    "spam": "Спам", "fraud": "Мошенничество", "prohibited": "Запрещённый товар",
    "wrong_category": "Не та категория", "other": "Другое",
}


@router.get("/api/config")
async def app_config():
    """Имя бота нужно клиенту для ссылки «Поделиться»."""
    return {"bot_username": await get_bot_username(), "app_short_name": MINIAPP_SHORT_NAME or None}


# --- СОХРАНЁННЫЕ ПОИСКИ ---
@router.get("/api/saved-searches", response_model=list[SavedSearchOut])
async def list_saved_searches(user_data: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(SavedSearch).where(SavedSearch.telegram_id == user_data["id"]).order_by(SavedSearch.id.desc())
    )).scalars().all()
    return rows


@router.post("/api/saved-searches", response_model=SavedSearchOut)
async def create_saved_search(data: SavedSearchIn, user_data: dict = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    q = clean_str(data.q, 120)
    city = clean_str(data.city, 128)
    if not q and not data.category_id:
        raise HTTPException(400, "Введите запрос или выберите категорию")
    if data.category_id and not await db.get(Category, data.category_id):
        raise HTTPException(400, "Категория не найдена")
    tg_id = user_data["id"]
    existing = (await db.execute(select(SavedSearch).where(SavedSearch.telegram_id == tg_id))).scalars().all()
    for e in existing:  # такой же поиск уже сохранён
        if (e.q or "").casefold() == (q or "").casefold() and e.category_id == data.category_id \
                and e.price_max == data.price_max and (e.city or "") == (city or ""):
            return e
    if len(existing) >= MAX_SAVED_SEARCHES:
        raise HTTPException(400, f"Можно сохранить не больше {MAX_SAVED_SEARCHES} поисков")
    row = SavedSearch(telegram_id=tg_id, q=q, category_id=data.category_id, price_max=data.price_max, city=city)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/api/saved-searches/{search_id}", response_model=dict)
async def delete_saved_search(search_id: int, user_data: dict = Depends(get_current_user),
                              db: AsyncSession = Depends(get_session)):
    row = await db.get(SavedSearch, search_id)
    if not row or row.telegram_id != user_data["id"]:
        raise HTTPException(404, "Поиск не найден")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


# --- ЖАЛОБЫ ---
@router.post("/api/reports", response_model=dict)
async def create_report(data: ReportIn, request: Request, ctx: Ctx = Depends(get_registered),
                        db: AsyncSession = Depends(get_session)):
    tg_id = ctx.user.telegram_id
    rate_limit(client_key(request, tg_id, "report"), limit=5, window=60)
    if data.reason not in REPORT_REASONS:
        raise HTTPException(400, "Некорректная причина")
    if data.target_type == "item":
        target = await db.get(MarketplaceItem, data.target_id)
        title = target.title if target else None
        if target and target.user_id == ctx.user.id:
            raise HTTPException(400, "Нельзя пожаловаться на своё объявление")
    else:
        target = await db.get(Shop, data.target_id)
        title = target.name if target else None
        if target and target.owner_telegram_id == tg_id:
            raise HTTPException(400, "Нельзя пожаловаться на свой магазин")
    if not target:
        raise HTTPException(404, "Объект жалобы не найден")
    dup = (await db.execute(select(Report.id).where(
        Report.reporter_telegram_id == tg_id, Report.target_type == data.target_type,
        Report.target_id == data.target_id, Report.status == "new",
    ))).first()
    if dup:
        return {"ok": True, "duplicate": True}
    db.add(Report(
        reporter_telegram_id=tg_id, target_type=data.target_type, target_id=data.target_id,
        reason=data.reason, details=clean_str(data.details, 1000),
    ))
    await db.commit()
    kind = "объявление" if data.target_type == "item" else "магазин"
    await _notify_admins(db, (
        f"🚩 Жалоба на {kind} #{data.target_id}: «{title}»\n"
        f"Причина: {REPORT_LABELS[data.reason]}\nОт: @{ctx.username}"
        + (f"\n{clean_str(data.details, 300)}" if data.details else "")
    ))
    return {"ok": True}


async def _close_all(db: AsyncSession, r: Report) -> None:
    """Закрывает жалобу и все остальные открытые жалобы на тот же объект."""
    await db.execute(
        update(Report).where(Report.target_type == r.target_type, Report.target_id == r.target_id,
                             Report.status == "new").values(status="closed")
    )
    r.status = "closed"


@admin_router.get("", response_model=list[dict])
async def admin_list_reports(admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Report).order_by(case((Report.status == "new", 0), else_=1), Report.id.desc()).limit(200))).scalars().all()
    item_ids = {r.target_id for r in rows if r.target_type == "item"}
    shop_ids = {r.target_id for r in rows if r.target_type == "shop"}
    items = {i.id: i for i in (await db.execute(select(MarketplaceItem).where(MarketplaceItem.id.in_(item_ids)))).scalars().all()} if item_ids else {}
    shops = {s.id: s for s in (await db.execute(select(Shop).where(Shop.id.in_(shop_ids)))).scalars().all()} if shop_ids else {}
    users = {u.telegram_id: u for u in (await db.execute(
        select(User).where(User.telegram_id.in_({r.reporter_telegram_id for r in rows}))
    )).scalars().all()} if rows else {}
    out = []
    for r in rows:
        if r.target_type == "item":
            it = items.get(r.target_id)
            title, alive = (it.title, it.is_active) if it else ("(удалено)", False)
        else:
            sh = shops.get(r.target_id)
            title, alive = (sh.name, sh.status == "active") if sh else ("(удалён)", False)
        u = users.get(r.reporter_telegram_id)
        out.append({
            "id": r.id, "target_type": r.target_type, "target_id": r.target_id, "title": title, "alive": alive,
            "reason": REPORT_LABELS.get(r.reason, r.reason), "details": r.details, "status": r.status,
            "reporter": f"@{u.username}" if u and u.username else str(r.reporter_telegram_id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


@admin_router.post("/{report_id}/close", response_model=dict)
async def admin_close_report(report_id: int, admin: dict = Depends(get_admin_user), db: AsyncSession = Depends(get_session)):
    r = await db.get(Report, report_id)
    if not r:
        raise HTTPException(404, "Жалоба не найдена")
    await _close_all(db, r)
    await db.commit()
    return {"ok": True}


@admin_router.post("/{report_id}/remove-item", response_model=dict)
async def admin_remove_reported_item(report_id: int, admin: dict = Depends(get_admin_user),
                                     db: AsyncSession = Depends(get_session)):
    """Скрыть объявление, на которое пожаловались (мягкое удаление), и закрыть жалобу."""
    r = await db.get(Report, report_id)
    if not r or r.target_type != "item":
        raise HTTPException(404, "Жалоба не найдена")
    item = await db.get(MarketplaceItem, r.target_id)
    if item:
        item.is_active = False
    await _close_all(db, r)
    await db.commit()
    return {"ok": True}
