"""Профиль продавца: рейтинг, продажи, «на Orient с…», значок «отвечает быстро»."""
from datetime import timezone
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ItemFav, ItemMessage, MarketplaceItem, Shop, ShopFollow, ShopOrder, User

FAST_REPLY_SECONDS = 3 * 3600
FAST_REPLY_MIN_SAMPLES = 3


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


async def _reply_delays(db: AsyncSession, seller_tg_ids: set[int], item_ids: list[int]) -> list[float]:
    """Сколько секунд продавец отвечал на первое сообщение покупателя в каждой ветке."""
    if not item_ids:
        return []
    rows = (await db.execute(
        select(ItemMessage).where(ItemMessage.item_id.in_(item_ids)).order_by(ItemMessage.id.desc()).limit(1000)
    )).scalars().all()
    threads: dict[tuple[int, int], list[ItemMessage]] = {}
    for m in reversed(rows):
        threads.setdefault((m.item_id, m.buyer_telegram_id), []).append(m)
    delays: list[float] = []
    for msgs in threads.values():
        first_buyer = next((m for m in msgs if m.sender_telegram_id not in seller_tg_ids), None)
        if not first_buyer:
            continue
        reply = next((m for m in msgs if m.sender_telegram_id in seller_tg_ids and m.id > first_buyer.id), None)
        if reply and reply.created_at and first_buyer.created_at:
            delays.append((_aware(reply.created_at) - _aware(first_buyer.created_at)).total_seconds())
    return delays


async def seller_stats(db: AsyncSession, owner_tg_id: int, shop: Shop | None = None) -> dict:
    owner = (await db.execute(select(User).where(User.telegram_id == owner_tg_id))).scalar_one_or_none()
    cond = MarketplaceItem.shop_id == shop.id if shop else (
        (MarketplaceItem.user_id == owner.id) & MarketplaceItem.shop_id.is_(None)
    ) if owner else None
    if cond is None:
        return {}
    item_ids = [i for (i,) in (await db.execute(select(MarketplaceItem.id).where(cond))).all()]
    active = (await db.execute(
        select(func.count(MarketplaceItem.id)).where(
            cond, MarketplaceItem.is_active.is_(True), MarketplaceItem.moderation_status == "approved",
            MarketplaceItem.sold_at.is_(None))
    )).scalar() or 0
    if shop:
        sales = (await db.execute(
            select(func.count(ShopOrder.id)).where(ShopOrder.shop_id == shop.id, ShopOrder.status == "completed")
        )).scalar() or 0
        followers = (await db.execute(
            select(func.count(ShopFollow.id)).where(ShopFollow.shop_id == shop.id)
        )).scalar() or 0
        ids = {owner_tg_id} | ({shop.manager_telegram_id} if shop.manager_telegram_id else set())
        since = shop.created_at
    else:
        sales = (await db.execute(
            select(func.count(MarketplaceItem.id)).where(cond, MarketplaceItem.sold_at.isnot(None))
        )).scalar() or 0
        followers, ids, since = 0, {owner_tg_id}, owner.created_at if owner else None
    delays = await _reply_delays(db, ids, item_ids)
    fast = len(delays) >= FAST_REPLY_MIN_SAMPLES and median(delays) <= FAST_REPLY_SECONDS
    return {
        "since": since,
        "sales": sales,
        "active_ads": active,
        "followers": followers,
        "fast_reply": fast,
        "rating": round(shop.rating_avg, 1) if shop and shop.reviews_count else None,
        "reviews": shop.reviews_count if shop else 0,
    }
