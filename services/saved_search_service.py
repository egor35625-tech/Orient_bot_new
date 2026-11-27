"""Уведомления о новых объявлениях по сохранённым поискам."""
import asyncio
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from config import WEBAPP_URL
from database import async_session
from models import MarketplaceItem, SavedSearch
from services.market_service import item_load_options
from services.telegram_service import bot

logger = logging.getLogger(__name__)
MAX_RECIPIENTS = 200


def webapp_button(text: str, query: str) -> InlineKeyboardMarkup | None:
    """Кнопка, открывающая мини-приложение с параметром (?item=1 / ?order=1). Нужен https."""
    if not WEBAPP_URL.startswith("https://"):
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{WEBAPP_URL}/?{query}"))]
    ])


async def notify_saved_searches(item_id: int) -> None:
    """Вызывается в фоне после публикации объявления; ошибки не должны ломать запрос."""
    try:
        async with async_session() as db:
            item = (await db.execute(
                select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
            )).scalar_one_or_none()
            if not item or not item.is_active or item.sold_at is not None or item.moderation_status != "approved":
                return
            shop = item.shop
            if shop and shop.status != "active":
                return
            subs = (await db.execute(
                select(SavedSearch).where(
                    (SavedSearch.category_id.is_(None)) | (SavedSearch.category_id == item.category_id)
                )
            )).scalars().all()

        city = (shop.city if shop else item.city) or ""
        owner_tg = item.user.telegram_id if item.user else None
        blob = item.search_text or ""
        price = f"{item.price} {item.currency}" if item.price else "Цена договорная"
        text = f"🔔 Новое объявление по вашему поиску\n\n{item.title}\n{price}"
        markup = webapp_button("Открыть объявление", f"item={item.id}")

        notified: set[int] = set()
        for sub in subs:
            if sub.telegram_id == owner_tg or sub.telegram_id in notified:
                continue
            if sub.q and sub.q.casefold() not in blob:
                continue
            if sub.price_max is not None and (item.price_value is None or item.price_value > sub.price_max):
                continue
            if sub.city and city.casefold() != sub.city.casefold():
                continue
            notified.add(sub.telegram_id)
            try:
                await bot.send_message(sub.telegram_id, text, reply_markup=markup)
            except Exception as e:
                logger.warning("Saved-search notify failed for %s: %s", sub.telegram_id, e)
            if len(notified) >= MAX_RECIPIENTS:
                break
            await asyncio.sleep(0.05)
    except Exception:
        logger.exception("notify_saved_searches failed for item %s", item_id)
