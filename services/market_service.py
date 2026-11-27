from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import selectinload

from models import MarketplaceItem, Shop, User


def item_load_options():
    """Всё, что нужно для item_out, загружается заранее (async: ленивая загрузка запрещена)."""
    return (
        selectinload(MarketplaceItem.photos),
        selectinload(MarketplaceItem.user).selectinload(User.profile),
        selectinload(MarketplaceItem.shop),
    )


BUMP_COOLDOWN = timedelta(hours=24)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite отдаёт время без часового пояса — считаем его UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def bump_wait_seconds(item: MarketplaceItem) -> int:
    last = _aware(item.bumped_at) or _aware(item.created_at)
    if last is None:
        return 0
    left = (last + BUMP_COOLDOWN) - datetime.now(timezone.utc)
    return max(0, int(left.total_seconds()))


PRICE_DROP_SHOW = timedelta(days=14)


def _price_drop_recent(item: MarketplaceItem) -> bool:
    at = _aware(item.price_dropped_at)
    return bool(at and datetime.now(timezone.utc) - at < PRICE_DROP_SHOW)


def item_out(item: MarketplaceItem, viewer_tg_id: int | None = None) -> dict:
    """Собирает ответ. У объявлений магазина контакты/город/доставка берутся из магазина,
    поэтому правки магазина (после модерации) сразу видны во всех его объявлениях."""
    shop: Shop | None = item.shop
    mine = bool(viewer_tg_id and item.user and item.user.telegram_id == viewer_tg_id)
    profile = item.user.profile if item.user else None
    if shop:
        seller = shop.manager_username or (profile.tg_username if profile else None)
        phone, mode = shop.phone or "", shop.contact_mode or "messages"
        city, delivery, pickup = shop.city, shop.delivery_methods, shop.pickup_address
    else:
        seller = profile.tg_username if profile else None
        phone, mode = item.phone or "", item.contact_mode or "messages"
        city, delivery, pickup = item.city, item.delivery_methods, item.pickup_address
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "category_id": item.category_id,
        "title": item.title,
        "description": item.description or "",
        "price": item.price,
        "currency": item.currency,
        "phone": phone,
        "contact_mode": mode if phone else "messages",
        "city": city,
        "country": "BY",
        "delivery_methods": delivery,
        "pickup_address": pickup,
        "views": item.views or 0,
        "sizes": item.sizes,
        "colors": item.colors,
        "sold": item.sold_at is not None,
        "brand": item.brand,
        "condition": item.condition,
        "old_price": item.old_price if item.old_price and _price_drop_recent(item) else None,
        "moderation_status": item.moderation_status or "approved",
        "reject_reason": item.reject_reason if mine else None,
        "bumped_at": item.bumped_at,
        "bump_wait": bump_wait_seconds(item) if mine else 0,
        "favs": (item.favs or 0) if mine else 0,
        "contacts": (item.contacts or 0) if mine else 0,
        "created_at": item.created_at,
        "seller_username": seller,
        "is_mine": mine,
        "shop": {"id": shop.id, "name": shop.name, "logo": shop.logo, "status": shop.status} if shop else None,
        "photos": [{"id": p.id, "file_path": p.file_path} for p in item.photos],
    }
