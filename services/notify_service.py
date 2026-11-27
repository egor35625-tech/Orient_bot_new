"""Уведомления от бота: подписчикам магазина и тем, у кого товар в избранном."""
import asyncio
import logging

from sqlalchemy import select

from config import MINIAPP_SHORT_NAME
from database import async_session
from models import ItemFav, MarketplaceItem, ShopFollow
from services.market_service import item_load_options
from services.saved_search_service import webapp_button
from services.telegram_service import bot

logger = logging.getLogger(__name__)
MAX_RECIPIENTS = 300

_bot_username: str | None = None


async def get_bot_username() -> str | None:
    global _bot_username
    if _bot_username is None:
        try:
            _bot_username = (await bot.get_me()).username or None
        except Exception:
            _bot_username = None  # не кэшируем неудачу
    return _bot_username


async def app_link(kind: str, obj_id: int) -> str | None:
    """Ссылка на объявление/магазин (kind = item | shop), которую можно отправить кому угодно.

    * Задано MINIAPP_SHORT_NAME -> прямая ссылка мини-приложения: открывается сразу нужная страница.
    * Иначе -> https://t.me/<бот>?start=item_5: бот отвечает кнопкой «Открыть», а кнопка бота
      всегда открывает именно нужную страницу (не зависит от настроек Main App в BotFather).
    """
    bot_name = await get_bot_username()
    if not bot_name:
        return None
    if MINIAPP_SHORT_NAME:
        return f"https://t.me/{bot_name}/{MINIAPP_SHORT_NAME}?startapp={kind}_{obj_id}"
    return f"https://t.me/{bot_name}?start={kind}_{obj_id}"


async def _send_all(tg_ids: list[int], text: str, markup) -> None:
    for tg in tg_ids[:MAX_RECIPIENTS]:
        try:
            await bot.send_message(tg, text, reply_markup=markup)
        except Exception as e:
            logger.warning("notify failed for %s: %s", tg, e)
        await asyncio.sleep(0.05)


async def _load(db, item_id: int) -> MarketplaceItem | None:
    item = (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()
    if not item or not item.is_active or item.sold_at is not None or item.moderation_status != "approved":
        return None
    if item.shop and item.shop.status != "active":
        return None
    return item


async def notify_followers(item_id: int) -> None:
    """Новый товар магазина -> его подписчикам."""
    try:
        async with async_session() as db:
            item = await _load(db, item_id)
            if not item or not item.shop_id:
                return
            owner = item.user.telegram_id if item.user else None
            ids = [t for (t,) in (await db.execute(
                select(ShopFollow.telegram_id).where(ShopFollow.shop_id == item.shop_id)
            )).all() if t != owner]
            shop_name, title = item.shop.name, item.title
            price = f"{item.price} {item.currency}" if item.price else "Цена договорная"
        if ids:
            text = f"🆕 Новинка в магазине «{shop_name}»\n\n{title}\n{price}"
            await _send_all(ids, text, webapp_button("Открыть товар", f"item={item_id}"))
    except Exception:
        logger.exception("notify_followers failed for item %s", item_id)


async def notify_price_drop(item_id: int, old_price: str) -> None:
    """Цена снижена -> тем, у кого товар в избранном."""
    try:
        async with async_session() as db:
            item = await _load(db, item_id)
            if not item:
                return
            owner = item.user.telegram_id if item.user else None
            ids = [t for (t,) in (await db.execute(
                select(ItemFav.telegram_id).where(ItemFav.item_id == item_id)
            )).all() if t != owner]
            title, cur = item.title, f"{item.price} {item.currency}" if item.price else "договорная"
        if ids:
            text = f"📉 Цена снижена\n\n{title}\nБыло {old_price} → стало {cur}"
            await _send_all(ids, text, webapp_button("Открыть товар", f"item={item_id}"))
    except Exception:
        logger.exception("notify_price_drop failed for item %s", item_id)
