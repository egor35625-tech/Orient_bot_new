"""Чат покупателя с продавцом по объявлению (до оформления заказа)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Ctx, get_current_user, get_registered
from database import get_session
from models import ItemMessage, MarketplaceItem, Shop, User
from schemas import ChatIn
from services.market_service import item_load_options
from services.rate_limit import rate_limit
from services.saved_search_service import webapp_button
from services.telegram_service import bot

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _seller_ids(item: MarketplaceItem) -> set[int]:
    ids = {item.user.telegram_id} if item.user else set()
    if item.shop and item.shop.manager_telegram_id:
        ids.add(item.shop.manager_telegram_id)
    return ids


async def _load_item(db: AsyncSession, item_id: int, me: int) -> MarketplaceItem:
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if not item or not item.is_active or (item.shop and item.shop.status != "active"):
        raise HTTPException(404, "Объявление не найдено")
    # Объявление на модерации/отклонённое покупателям недоступно — только продавцу.
    if item.moderation_status != "approved" and me not in _seller_ids(item):
        raise HTTPException(404, "Объявление не найдено")
    return item


def _resolve_buyer(item: MarketplaceItem, me: int, buyer: int | None) -> int:
    """Покупатель — всегда сам пользователь; продавец указывает, с каким покупателем переписка."""
    if me in _seller_ids(item):
        if not buyer or buyer in _seller_ids(item):
            raise HTTPException(400, "Не указан покупатель")
        return buyer
    return me


def _msg_out(m: ItemMessage, me: int) -> dict:
    return {"id": m.id, "text": m.text, "mine": m.sender_telegram_id == me, "created_at": m.created_at}


@router.get("", response_model=list[dict])
async def inbox(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Мои переписки: и как покупателя, и как продавца."""
    me = user["id"]
    my_item_ids = (
        select(MarketplaceItem.id)
        .join(User, MarketplaceItem.user_id == User.id)
        .outerjoin(Shop, MarketplaceItem.shop_id == Shop.id)
        .where((User.telegram_id == me) | (Shop.manager_telegram_id == me))
    )
    last = (
        select(ItemMessage.item_id, ItemMessage.buyer_telegram_id, func.max(ItemMessage.id).label("mid"))
        .where((ItemMessage.buyer_telegram_id == me) | ItemMessage.item_id.in_(my_item_ids))
        .group_by(ItemMessage.item_id, ItemMessage.buyer_telegram_id).subquery()
    )
    rows = (await db.execute(
        select(ItemMessage).join(last, ItemMessage.id == last.c.mid).order_by(ItemMessage.id.desc()).limit(100)
    )).scalars().all()
    out = []
    for m in rows:
        item = (await db.execute(
            select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == m.item_id)
        )).scalar_one_or_none()
        if not item or not item.is_active or (item.shop and item.shop.status != "active"):
            continue
        role = "buyer" if m.buyer_telegram_id == me else "seller"
        if role == "seller" and me not in _seller_ids(item):
            continue
        if role == "buyer" and item.moderation_status != "approved":
            continue
        buyer_name = None
        if role == "seller":
            b = (await db.execute(select(User).where(User.telegram_id == m.buyer_telegram_id))).scalar_one_or_none()
            buyer_name = (b.first_name or b.username) if b else None
        out.append({
            "item_id": item.id, "title": item.title, "role": role, "buyer": m.buyer_telegram_id,
            "with": buyer_name or "Покупатель" if role == "seller" else (item.shop.name if item.shop else "Продавец"),
            "photo": item.photos[0].file_path if item.photos else None,
            "last_text": m.text[:120], "last_at": m.created_at, "last_mine": m.sender_telegram_id == me,
        })
    return out


@router.get("/{item_id}", response_model=dict)
async def thread(item_id: int, buyer: int | None = None, user: dict = Depends(get_current_user),
                 db: AsyncSession = Depends(get_session)):
    me = user["id"]
    item = await _load_item(db, item_id, me)
    b = _resolve_buyer(item, me, buyer)
    msgs = (await db.execute(
        select(ItemMessage).where(ItemMessage.item_id == item_id, ItemMessage.buyer_telegram_id == b)
        .order_by(ItemMessage.id.asc()).limit(300)
    )).scalars().all()
    return {
        "item": {"id": item.id, "title": item.title, "price": item.price, "currency": item.currency,
                 "photo": item.photos[0].file_path if item.photos else None},
        "role": "seller" if me in _seller_ids(item) else "buyer",
        "buyer": b,
        "messages": [_msg_out(m, me) for m in msgs],
    }


@router.post("/{item_id}", response_model=dict)
async def send(item_id: int, data: ChatIn, buyer: int | None = None, ctx: Ctx = Depends(get_registered),
               db: AsyncSession = Depends(get_session)):
    me = ctx.user.telegram_id
    rate_limit(f"chat:{me}", limit=20, window=60)
    text = (data.text or "").strip()
    if not text or len(text) > 2000:
        raise HTTPException(400, "Сообщение пустое или слишком длинное (до 2000 символов)")
    item = await _load_item(db, item_id, me)
    if item.sold_at is not None and me not in _seller_ids(item):
        raise HTTPException(400, "Товар уже продан")
    b = _resolve_buyer(item, me, buyer)
    if me != b and not (await db.execute(
        select(ItemMessage.id).where(
            ItemMessage.item_id == item_id, ItemMessage.buyer_telegram_id == b, ItemMessage.sender_telegram_id == b
        ).limit(1)
    )).first():
        raise HTTPException(400, "Первым в переписке пишет покупатель")
    m = ItemMessage(item_id=item_id, buyer_telegram_id=b, sender_telegram_id=me, text=text)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    # Уведомляем вторую сторону; если бот заблокирован — не ломаем отправку.
    sellers = _seller_ids(item)
    targets = sellers if me == b else {b}
    sender = "Покупатель" if me == b else (item.shop.name if item.shop else "Продавец")
    note = f"💬 {sender} по товару «{item.title}»:\n\n{text[:500]}"
    for t in targets:
        try:
            await bot.send_message(t, note, reply_markup=webapp_button("Ответить", f"chat={item_id}-{b}"))
        except Exception:
            pass
    return {"ok": True, "message": _msg_out(m, me)}
