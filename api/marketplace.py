import asyncio
from datetime import datetime, timezone

from fastapi.responses import Response
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Ctx, get_current_user, get_optional_user, get_registered, is_admin_id
from config import MAX_ITEM_PHOTOS
from database import get_session
from models import Category, ItemFav, MarketplaceItem, MarketplacePhoto, Shop, User
from schemas import FavIn, MarketItemOut, MyItemsOut, SoldIn
from services.brands import brand_key, canonical_brand, clean_brand, suggest_from_known
from services.market_service import bump_wait_seconds, item_load_options, item_out
from services.moderation_service import APPROVED, PENDING, REJECTED, notify_admins_new_item
from services.media import delete_media, read_media, save_image
from services.notify_service import app_link, notify_price_drop
from services.seller_service import seller_stats
from services.share_card import render_card
from services.rate_limit import client_key, rate_limit
from services.shop_service import release_expired_blocks
from services.text import (
    DELIVERY_ALLOWED, clean_contact_mode, clean_delivery, clean_options, clean_phone, clean_str,
    like_pattern, make_search_text, parse_price,
)

router = APIRouter(prefix="/api/market", tags=["market"])
categories_router = APIRouter(tags=["categories"])

CURRENCIES = ("BYN", "USD", "EUR", "RUB")
CONDITIONS = ("new_tag", "new", "excellent", "good", "fair", "poor")
CONDITION_LABELS = {
    "new_tag": "Новое с биркой", "new": "Новое без бирки", "excellent": "Отличное",
    "good": "Хорошее", "fair": "Нормально", "poor": "Плохое",
}
_LEGACY_CONDITIONS = {"like_new": "excellent", "used": "good"}  # значения прежней версии (старые клиенты)


@categories_router.get("/api/categories")
async def list_categories(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Category).order_by(Category.position, Category.id))).scalars().all()
    return [{"id": c.id, "parent_id": c.parent_id, "name": c.name, "icon": c.icon, "position": c.position} for c in rows]


def _visible_filter():
    """Активные и прошедшие модерацию объявления; объявления заблокированных магазинов скрыты."""
    return (
        MarketplaceItem.is_active.is_(True),
        MarketplaceItem.moderation_status == APPROVED,
        MarketplaceItem.type == "sell",
        MarketplaceItem.sold_at.is_(None),
        (MarketplaceItem.shop_id.is_(None)) | (Shop.status == "active"),
    )


# У объявлений магазина город и доставка берутся из магазина.
_EFF_CITY = case((MarketplaceItem.shop_id.isnot(None), Shop.city), else_=MarketplaceItem.city)
_EFF_DELIVERY = case((MarketplaceItem.shop_id.isnot(None), Shop.delivery_methods), else_=MarketplaceItem.delivery_methods)


@router.get("", response_model=list[MarketItemOut])
async def list_items(
    q: str | None = None,
    category_id: int | None = None,
    shop_id: int | None = None,
    price_min: float | None = Query(None, ge=0),
    price_max: float | None = Query(None, ge=0),
    city: str | None = None,
    delivery: str | None = None,
    brand: list[str] = Query(default=[]),
    condition: str | None = None,
    sort: str = "new",
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    viewer: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_session),
):
    await release_expired_blocks(db)
    query = (
        select(MarketplaceItem)
        .outerjoin(Shop, MarketplaceItem.shop_id == Shop.id)
        .options(*item_load_options())
        .where(*_visible_filter())
    )
    if category_id:
        query = query.where(MarketplaceItem.category_id == category_id)
    if shop_id:
        query = query.where(MarketplaceItem.shop_id == shop_id)
    if q and q.strip():
        query = query.where(MarketplaceItem.search_text.like(like_pattern(q), escape="\\"))
    if price_min is not None:
        query = query.where(MarketplaceItem.price_value >= price_min)
    if price_max is not None:
        query = query.where(MarketplaceItem.price_value <= price_max)
    if city and city.strip():
        query = query.where(_EFF_CITY == city.strip())
    if delivery in DELIVERY_ALLOWED:
        query = query.where(_EFF_DELIVERY.like(f"%{delivery}%"))
    keys = [k for k in (brand_key(b) for b in brand[:30]) if k]
    if keys:
        query = query.where(MarketplaceItem.brand_key.in_(keys))
    condition = _LEGACY_CONDITIONS.get(condition or "", condition)
    if condition in CONDITIONS:
        query = query.where(MarketplaceItem.condition == condition)
    if sort == "price_asc":
        query = query.order_by(MarketplaceItem.price_value.asc().nullslast(), MarketplaceItem.id.desc())
    elif sort == "price_desc":
        query = query.order_by(MarketplaceItem.price_value.desc().nullslast(), MarketplaceItem.id.desc())
    elif sort == "views":
        query = query.order_by(MarketplaceItem.views.desc(), MarketplaceItem.id.desc())
    else:
        # «Поднятые» объявления считаются свежими с момента поднятия.
        query = query.order_by(
            func.coalesce(MarketplaceItem.bumped_at, MarketplaceItem.created_at).desc(), MarketplaceItem.id.desc()
        )
    items = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    viewer_id = viewer["id"] if viewer else None
    return [item_out(i, viewer_id) for i in items]


@router.get("/my", response_model=MyItemsOut)
async def my_items(ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(
        select(MarketplaceItem)
        .options(*item_load_options())
        .where(MarketplaceItem.user_id == ctx.user.id, MarketplaceItem.is_active.is_(True), MarketplaceItem.type == "sell")
        .order_by(MarketplaceItem.created_at.desc(), MarketplaceItem.id.desc())
    )).scalars().all()
    out = [item_out(i, ctx.user.telegram_id) for i in rows]
    return {"private": [i for i in out if not i["shop_id"]], "shop": [i for i in out if i["shop_id"]]}


@router.get("/cities", response_model=list[dict])
async def list_cities(db: AsyncSession = Depends(get_session)):
    """Города, в которых есть активные объявления (для фильтра)."""
    rows = (await db.execute(
        select(_EFF_CITY.label("city"), func.count(MarketplaceItem.id))
        .select_from(MarketplaceItem).outerjoin(Shop, MarketplaceItem.shop_id == Shop.id)
        .where(*_visible_filter(), _EFF_CITY.isnot(None))
        .group_by(_EFF_CITY).order_by(func.count(MarketplaceItem.id).desc()).limit(50)
    )).all()
    return [{"city": c, "count": n} for c, n in rows if c]


@router.get("/brands", response_model=list[dict])
async def list_brands(
    q: str | None = None, category_id: int | None = None, db: AsyncSession = Depends(get_session),
):
    """Бренды активных объявлений со счётчиками — для вкладки «Бренды» в фильтре."""
    cond = [*_visible_filter(), MarketplaceItem.brand_key.isnot(None)]
    if category_id:
        cond.append(MarketplaceItem.category_id == category_id)
    rows = (await db.execute(
        select(MarketplaceItem.brand_key, func.min(MarketplaceItem.brand), func.count(MarketplaceItem.id))
        .outerjoin(Shop, MarketplaceItem.shop_id == Shop.id)
        .where(*cond).group_by(MarketplaceItem.brand_key)
        .order_by(func.count(MarketplaceItem.id).desc(), func.min(MarketplaceItem.brand)).limit(300)
    )).all()
    out = [{"name": name, "count": n} for _, name, n in rows if name]
    k = brand_key(q) if q else None
    if k:
        out = [b for b in out if k in brand_key(b["name"])]
    return out


@router.get("/brands/suggest", response_model=list[str])
async def suggest_brands(q: str = Query("", max_length=40), db: AsyncSession = Depends(get_session)):
    """Автозаполнение для продавца: по первым буквам предлагает бренды (из базы и словаря популярных)."""
    k = brand_key(q)
    if not k:
        return []
    rows = (await db.execute(
        select(func.min(MarketplaceItem.brand), func.count(MarketplaceItem.id))
        .where(MarketplaceItem.brand_key.isnot(None), MarketplaceItem.is_active.is_(True),
               MarketplaceItem.moderation_status == APPROVED)
        .group_by(MarketplaceItem.brand_key).order_by(func.count(MarketplaceItem.id).desc()).limit(500)
    )).all()
    seen: dict[str, str] = {}
    starts: list[str] = []
    contains: list[str] = []
    for name, _ in rows:           # сначала то, что уже вводили другие продавцы (по популярности)
        bk = brand_key(name)
        if not bk or bk in seen:
            continue
        seen[bk] = name
        if bk.startswith(k) or any(w.startswith(k) for w in bk.split(" ")):
            starts.append(name)
        elif k in bk:
            contains.append(name)
    for name in suggest_from_known(q, 12):
        bk = brand_key(name)
        if bk not in seen:
            seen[bk] = name
            (starts if bk.startswith(k) or any(w.startswith(k) for w in bk.split(" ")) else contains).append(name)
    return (starts + contains)[:8]


@router.get("/{item_id}", response_model=MarketItemOut)
async def get_item(item_id: int, viewer: dict | None = Depends(get_optional_user), db: AsyncSession = Depends(get_session)):
    """Одно объявление (ссылки из уведомлений и «Поделиться»). Проданное остаётся доступным по ссылке."""
    await release_expired_blocks(db)
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    viewer_id = viewer["id"] if viewer else None
    if not item or not item.is_active:
        raise HTTPException(404, "Объявление не найдено")
    mine = bool(viewer_id and item.user and item.user.telegram_id == viewer_id)
    if not mine and item.shop and item.shop.status != "active":
        raise HTTPException(404, "Объявление не найдено")
    # Не прошедшее модерацию видят только автор и модераторы.
    if item.moderation_status != APPROVED and not mine and not await is_admin_id(db, viewer_id):
        raise HTTPException(404, "Объявление не найдено")
    return item_out(item, viewer_id)


async def _owned_shop(db: AsyncSession, tg_id: int, shop_id: int) -> Shop:
    shop = await db.get(Shop, shop_id)
    # Добавлять и менять объявления магазина может только его владелец (не менеджер).
    if not shop or shop.owner_telegram_id != tg_id:
        raise HTTPException(403, "Объявления в магазин может добавлять только его владелец")
    if shop.status != "active":
        raise HTTPException(403, "Магазин заблокирован модератором")
    return shop


async def _resolve_brand(db: AsyncSession, raw: str | None) -> tuple[str | None, str | None]:
    try:
        name = clean_brand(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not name:
        return None, None
    key = brand_key(name)
    existing = (await db.execute(
        select(MarketplaceItem.brand).where(MarketplaceItem.brand_key == key).limit(1)
    )).scalar_one_or_none()
    pretty = canonical_brand(name, {key: existing} if existing else None)
    return pretty, key


def _clean_condition(raw: str | None) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = _LEGACY_CONDITIONS.get(raw, raw)
    if raw not in CONDITIONS:
        raise HTTPException(400, "Некорректное состояние товара")
    return raw


def _num(text: str | None) -> float:
    try:
        return float(text or 0)
    except ValueError:
        return 0.0


def _to_int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise HTTPException(400, "Некорректный числовой параметр")
    return int(raw)


def _validate_common(title: str, description: str, price: str | None, currency: str, category_id: str | None):
    title = title.strip()
    if len(title) < 2:
        raise HTTPException(400, "Введите название (минимум 2 символа)")
    if len(title) > 120:
        raise HTTPException(400, "Название слишком длинное (максимум 120 символов)")
    if len(description) > 4000:
        raise HTTPException(400, "Описание слишком длинное (максимум 4000 символов)")
    if currency not in CURRENCIES:
        raise HTTPException(400, "Некорректная валюта")
    try:
        price_text, price_value = parse_price(price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return title, price_text, price_value


async def _check_category(db: AsyncSession, category_id: int | None):
    if category_id and not await db.get(Category, category_id):
        raise HTTPException(400, "Категория не найдена")


def _contact_fields(shop: Shop | None, phone: str | None, contact_mode: str | None, city: str | None,
                    delivery_methods: str | None, pickup_address: str | None) -> dict:
    if shop:  # контакты и доставка магазина берутся из самого магазина
        return {"phone": "", "contact_mode": "messages", "city": None, "delivery_methods": None, "pickup_address": None}
    try:
        phone_c = clean_phone(phone)
        mode = clean_contact_mode(contact_mode, phone_c)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "phone": phone_c, "contact_mode": mode, "city": clean_str(city, 128),
        "delivery_methods": clean_delivery(delivery_methods), "pickup_address": clean_str(pickup_address, 500),
    }


@router.post("", response_model=dict)
async def create_item(
    request: Request, background: BackgroundTasks,
    title: str = Form(...), description: str = Form(""),
    price: str | None = Form(None), currency: str = Form("BYN"),
    category_id: str | None = Form(None), shop_id: str | None = Form(None),
    phone: str | None = Form(None), contact_mode: str | None = Form("messages"),
    city: str | None = Form(None), delivery_methods: str | None = Form(None),
    pickup_address: str | None = Form(None),
    sizes: str | None = Form(None), colors: str | None = Form(None),
    brand: str | None = Form(None), condition: str | None = Form(None),
    photos: list[UploadFile] = File(default=[]),
    ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session),
):
    rate_limit(f"item-create:{ctx.user.telegram_id}", limit=20, window=3600)
    title, price_text, price_value = _validate_common(title, description.strip(), price, currency, category_id)
    category_id, shop_id = _to_int(category_id), _to_int(shop_id)
    await _check_category(db, category_id)
    shop = await _owned_shop(db, ctx.user.telegram_id, shop_id) if shop_id else None
    files = [f for f in photos if f and f.filename]
    if len(files) > MAX_ITEM_PHOTOS:
        raise HTTPException(400, f"Максимум {MAX_ITEM_PHOTOS} фото")
    fields = _contact_fields(shop, phone, contact_mode, city, delivery_methods, pickup_address)
    brand_name, brand_k = await _resolve_brand(db, brand)

    item = MarketplaceItem(
        user_id=ctx.user.id, shop_id=shop.id if shop else None, category_id=category_id, type="sell",
        title=title, description=description.strip(), price=price_text, price_value=price_value,
        currency=currency, search_text=make_search_text(title, description, brand_name), country="BY",
        brand=brand_name, brand_key=brand_k, condition=_clean_condition(condition),
        sizes=clean_options(sizes), colors=clean_options(colors), **fields,
        # Новое объявление не публикуется, пока его не одобрит модератор.
        moderation_status=PENDING, submitted_at=datetime.now(timezone.utc),
    )
    db.add(item)
    await db.flush()
    saved: list[str] = []
    try:
        for f in files:
            path = await save_image(f, f"market/{item.id}", db=db)
            saved.append(path)
            db.add(MarketplacePhoto(item_id=item.id, file_path=path))
        await db.commit()
    except Exception:
        await db.rollback()
        for p in saved:
            delete_media(p)
        raise
    # Модераторам уходит push в бот; подписчики поисков/магазина получат уведомление после одобрения.
    background.add_task(notify_admins_new_item, item.id)
    return {"ok": True, "id": item.id, "status": PENDING}


async def _get_own_item(db: AsyncSession, item_id: int, ctx: Ctx, check_shop: bool = True) -> MarketplaceItem:
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if not item or not item.is_active:
        raise HTTPException(404, "Объявление не найдено")
    if item.user_id != ctx.user.id:
        raise HTTPException(403, "Это не ваше объявление")
    if item.shop_id and check_shop:
        await _owned_shop(db, ctx.user.telegram_id, item.shop_id)
    return item


@router.put("/{item_id}", response_model=dict)
async def update_item(
    item_id: int, background: BackgroundTasks,
    title: str = Form(...), description: str = Form(""),
    price: str | None = Form(None), currency: str = Form("BYN"),
    category_id: str | None = Form(None),
    phone: str | None = Form(None), contact_mode: str | None = Form("messages"),
    city: str | None = Form(None), delivery_methods: str | None = Form(None),
    pickup_address: str | None = Form(None),
    sizes: str | None = Form(None), colors: str | None = Form(None),
    brand: str | None = Form(None), condition: str | None = Form(None),
    remove_photos: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session),
):
    item = await _get_own_item(db, item_id, ctx)
    title, price_text, price_value = _validate_common(title, description.strip(), price, currency, category_id)
    category_id = _to_int(category_id)
    await _check_category(db, category_id)

    remove_ids = {int(x) for x in remove_photos.split(",") if x.strip().isdigit()}
    to_remove = [p for p in item.photos if p.id in remove_ids]
    new_files = [f for f in photos if f and f.filename]
    if len(item.photos) - len(to_remove) + len(new_files) > MAX_ITEM_PHOTOS:
        raise HTTPException(400, f"Максимум {MAX_ITEM_PHOTOS} фото")

    fields = _contact_fields(item.shop, phone, contact_mode, city, delivery_methods, pickup_address)
    brand_name, brand_k = await _resolve_brand(db, brand)
    # Что видит покупатель и может использоваться для спама — после правки снова на модерацию.
    # Цена, состояние, размеры, цвета, город и доставка правятся без повторной проверки.
    content_changed = (
        title != item.title or description.strip() != (item.description or "") or brand_name != item.brand
        or category_id != item.category_id or fields["phone"] != (item.phone or "")
        or bool(to_remove) or bool(new_files)
    )
    resubmit = item.moderation_status == REJECTED or (item.moderation_status == APPROVED and content_changed)
    old_text, old_value = item.price, item.price_value
    currency_changed = item.currency != currency
    dropped = (
        old_value is not None and price_value is not None and price_value < old_value and item.currency == currency
    )
    item.title, item.description = title, description.strip()
    item.price, item.price_value, item.currency = price_text, price_value, currency
    item.category_id = category_id
    item.brand, item.brand_key, item.condition = brand_name, brand_k, _clean_condition(condition)
    item.search_text = make_search_text(title, description, brand_name)
    if dropped:
        item.old_price, item.price_dropped_at = old_text, datetime.now(timezone.utc)
    elif currency_changed or price_value is None or (item.old_price and price_value is not None and _num(item.old_price) <= price_value):
        item.old_price, item.price_dropped_at = None, None  # цену подняли обратно — «было» больше не показываем
    item.sizes, item.colors = clean_options(sizes), clean_options(colors)
    for k, v in fields.items():
        setattr(item, k, v)
    if resubmit:
        if item.moderation_status == APPROVED and item.published_at is None:
            item.published_at = item.created_at  # объявление до введения модерации уже было опубликовано
        item.moderation_status, item.reject_reason = PENDING, None
        item.submitted_at = datetime.now(timezone.utc)

    saved: list[str] = []
    try:
        for f in new_files:
            path = await save_image(f, f"market/{item.id}", db=db)
            saved.append(path)
            db.add(MarketplacePhoto(item_id=item.id, file_path=path))
        for p in to_remove:
            await db.delete(p)
        await db.commit()
    except Exception:
        await db.rollback()
        for p in saved:
            delete_media(p)
        raise
    for p in to_remove:
        delete_media(p.file_path)
    if resubmit:
        background.add_task(notify_admins_new_item, item.id)
    elif dropped and item.moderation_status == APPROVED:
        background.add_task(notify_price_drop, item.id, old_text)
    return {"ok": True, "id": item.id, "status": item.moderation_status}


@router.delete("/{item_id}", response_model=dict)
async def delete_item(item_id: int, ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session)):
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if not item or not item.is_active:
        raise HTTPException(404, "Объявление не найдено")
    if item.user_id != ctx.user.id:
        raise HTTPException(403, "Это не ваше объявление")
    # Мягкое удаление: заказы на этот товар остаются в истории покупок.
    item.is_active = False
    paths = [p.file_path for p in item.photos]
    for p in list(item.photos):
        await db.delete(p)
    await db.commit()
    for path in paths:
        delete_media(path)
    return {"ok": True}


@router.post("/{item_id}/view", response_model=dict)
async def count_view(item_id: int, request: Request, viewer: dict | None = Depends(get_optional_user),
                     db: AsyncSession = Depends(get_session)):
    # Один просмотр с одного клиента не чаще 3 раз за 10 минут — от накрутки.
    rate_limit(f"view:{item_id}:{client_key(request, viewer['id'] if viewer else None)}", limit=3, window=600)
    res = await db.execute(
        update(MarketplaceItem).where(
            MarketplaceItem.id == item_id, MarketplaceItem.is_active.is_(True), MarketplaceItem.moderation_status == APPROVED)
        .values(views=func.coalesce(MarketplaceItem.views, 0) + 1)
    )
    if not res.rowcount:
        raise HTTPException(404, "Объявление не найдено")
    await db.commit()
    return {"ok": True}


@router.post("/{item_id}/contact", response_model=dict)
async def count_contact(item_id: int, request: Request, viewer: dict | None = Depends(get_optional_user),
                        db: AsyncSession = Depends(get_session)):
    """Покупатель нажал «Написать» / «Позвонить» / «Купить» — считаем обращение для статистики продавца."""
    key = client_key(request, viewer["id"] if viewer else None)
    rate_limit(f"contact:{item_id}:{key}", limit=3, window=600)
    res = await db.execute(
        update(MarketplaceItem).where(
            MarketplaceItem.id == item_id, MarketplaceItem.is_active.is_(True), MarketplaceItem.moderation_status == APPROVED)
        .values(contacts=func.coalesce(MarketplaceItem.contacts, 0) + 1)
    )
    if not res.rowcount:
        raise HTTPException(404, "Объявление не найдено")
    await db.commit()
    return {"ok": True}


@router.post("/{item_id}/fav", response_model=dict)
async def count_fav(item_id: int, data: FavIn, request: Request, viewer: dict | None = Depends(get_optional_user),
                    db: AsyncSession = Depends(get_session)):
    """Избранное на сервере: нужно для счётчика у продавца и уведомлений о снижении цены.
    Повторный запрос с тем же состоянием ничего не меняет, поэтому счётчик не накрутить."""
    if not viewer:
        return {"ok": True}
    tg = viewer["id"]
    rate_limit(f"fav:{tg}", limit=60, window=60)
    if not (await db.execute(
        select(MarketplaceItem.id).where(
            MarketplaceItem.id == item_id, MarketplaceItem.is_active.is_(True), MarketplaceItem.moderation_status == APPROVED)
    )).first():
        raise HTTPException(404, "Объявление не найдено")
    row = (await db.execute(
        select(ItemFav).where(ItemFav.telegram_id == tg, ItemFav.item_id == item_id)
    )).scalar_one_or_none()
    delta = 0
    if data.on and not row:
        db.add(ItemFav(telegram_id=tg, item_id=item_id))
        delta = 1
    elif not data.on and row:
        await db.delete(row)
        delta = -1
    if delta:
        cur = func.coalesce(MarketplaceItem.favs, 0)
        await db.execute(
            update(MarketplaceItem).where(MarketplaceItem.id == item_id)
            .values(favs=case((cur + delta < 0, 0), else_=cur + delta))
        )
    try:
        await db.commit()
    except IntegrityError:  # двойное нажатие: параллельный запрос уже всё записал
        await db.rollback()
    return {"ok": True}


@router.get("/favs/mine", response_model=list[int])
async def my_server_favs(viewer: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """id избранных объявлений — чтобы после смены телефона сердечки были на месте."""
    rows = (await db.execute(
        select(ItemFav.item_id).join(MarketplaceItem, MarketplaceItem.id == ItemFav.item_id)
        .where(ItemFav.telegram_id == viewer["id"], MarketplaceItem.is_active.is_(True),
               MarketplaceItem.moderation_status == APPROVED)
    )).all()
    return [i for (i,) in rows]


@router.post("/{item_id}/sold", response_model=dict)
async def mark_sold(item_id: int, data: SoldIn, ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session)):
    """«Продано» убирает объявление из ленты, но оставляет в архиве продавца; можно вернуть."""
    item = await _get_own_item(db, item_id, ctx, check_shop=False)  # отметить «Продано» можно и при блокировке магазина
    item.sold_at = datetime.now(timezone.utc) if data.sold else None
    await db.commit()
    return {"ok": True, "sold": data.sold}


@router.post("/{item_id}/bump", response_model=dict)
async def bump_item(item_id: int, ctx: Ctx = Depends(get_registered), db: AsyncSession = Depends(get_session)):
    """Поднять объявление наверх ленты. Не чаще раза в 24 часа."""
    item = await _get_own_item(db, item_id, ctx)
    if item.moderation_status != APPROVED:
        raise HTTPException(400, "Поднять можно только опубликованное объявление (после модерации)")
    if item.sold_at is not None:
        raise HTTPException(400, "Проданное объявление поднять нельзя")
    wait = bump_wait_seconds(item)
    if wait:
        raise HTTPException(400, f"Поднять можно раз в 24 часа. Попробуйте через {(wait + 3599) // 3600} ч.")
    item.bumped_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/{item_id}/seller", response_model=dict)
async def item_seller(item_id: int, db: AsyncSession = Depends(get_session)):
    """Профиль продавца для карточки товара."""
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if not item or not item.is_active or item.moderation_status != APPROVED:
        raise HTTPException(404, "Объявление не найдено")
    if item.shop and item.shop.status != "active":
        raise HTTPException(404, "Объявление не найдено")
    owner_tg = item.user.telegram_id
    stats = await seller_stats(db, owner_tg, item.shop)
    profile = item.user.profile
    name = item.shop.name if item.shop else (item.user.first_name or (profile.tg_username if profile else None) or "Продавец")
    return {
        **stats, "name": name, "is_shop": bool(item.shop), "shop_id": item.shop_id,
        "logo": item.shop.logo if item.shop else None,
    }


async def _card_png(db: AsyncSession, item_id: int) -> tuple[bytes, MarketplaceItem]:
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if (not item or not item.is_active or item.moderation_status != APPROVED
            or (item.shop and item.shop.status != "active")):
        raise HTTPException(404, "Объявление не найдено")
    photo = None
    if item.photos:
        media = await read_media(item.photos[0].file_path)
        photo = media[0] if media else None
    price = f"{item.price} {item.currency}" if item.price else "Цена договорная"
    city = item.shop.city if item.shop else item.city
    meta = " · ".join(x for x in (city, CONDITION_LABELS.get(item.condition or "")) if x)
    png = await asyncio.to_thread(render_card, photo, item.title, price, meta, item.brand)
    return png, item


@router.get("/{item_id}/card.png")
async def item_card(item_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    rate_limit(f"card:{client_key(request)}", limit=30, window=60)
    png, _ = await _card_png(db, item_id)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=600"})


@router.post("/{item_id}/share", response_model=dict)
async def share_item(item_id: int, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    """Бот присылает пользователю красивую карточку товара со ссылкой — её можно переслать кому угодно."""
    rate_limit(f"share:{user['id']}", limit=10, window=60)
    png, item = await _card_png(db, item_id)
    link = await app_link("item", item.id)
    caption = f"{item.title}\n{item.price + ' ' + item.currency if item.price else 'Цена договорная'}" + (f"\n\n{link}" if link else "")
    markup = None
    if link:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть в Orient", url=link)]])
    from aiogram.types import BufferedInputFile
    from services.telegram_service import bot
    try:
        await bot.send_photo(user["id"], BufferedInputFile(png, filename="orient-item.png"),
                             caption=caption, reply_markup=markup)
    except Exception:
        raise HTTPException(400, "Не удалось отправить карточку. Откройте чат с ботом и нажмите «Start».")
    return {"ok": True}
