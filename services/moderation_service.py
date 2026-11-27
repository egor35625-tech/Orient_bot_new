"""Модерация объявлений.

Общая логика для веб-панели (/admin) и для бота (inline-кнопки в сообщении модератору):
* новое или изменённое объявление получает статус pending и не показывается в приложении;
* всем модераторам приходит сообщение бота с фото и кнопками «Одобрить» / «Отклонить»;
* после одобрения объявление публикуется, продавцу приходит уведомление, а подписчики сохранённых
  поисков и магазина получают сообщение о новинке (раньше это происходило сразу при создании).
"""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import item_moderation_keyboard
from config import WEBAPP_URL
from database import async_session
from models import Category, MarketplaceItem, Report, ShopApplication
from services.admin_service import get_admin_ids
from services.market_service import item_load_options
from services.media import read_media
from services.notify_service import notify_followers
from services.saved_search_service import notify_saved_searches, webapp_button
from services.telegram_service import bot

logger = logging.getLogger(__name__)

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"

# Результаты approve_item / reject_item.
OK, NOT_FOUND, CONFLICT = "ok", "not_found", "conflict"

_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """Фоновая задача, на которую держится ссылка (иначе сборщик мусора может её убить)."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _safe_send(chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:  # бот заблокирован / чат не начат
        logger.warning("Cannot send message to %s: %s", chat_id, e)


# --- Общие уведомления модераторам -------------------------------------------------------------
def panel_markup() -> InlineKeyboardMarkup | None:
    if not WEBAPP_URL.startswith("https://"):  # Telegram принимает web_app-кнопки только с https
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Открыть модерацию", web_app=WebAppInfo(url=f"{WEBAPP_URL}/admin"))]
    ])


async def notify_admins(text: str, with_panel_button: bool = True, db: AsyncSession | None = None) -> None:
    """Сообщение всем модераторам (ADMIN_IDS + добавленные через панель)."""
    markup = panel_markup() if with_panel_button else None
    for admin_id in await get_admin_ids(db):
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception as e:
            logger.warning("Cannot notify admin %s: %s", admin_id, e)


# --- Счётчики очереди -----------------------------------------------------------------------------
def _epoch(dt: datetime | None) -> float:
    if dt is None:
        return 0.0
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).timestamp()


async def pending_counts(db: AsyncSession) -> dict:
    """Сколько запросов ждут модератора + «метки» самых свежих (по ним панель понимает, что пришло новое,
    даже если параллельно кто-то уже обработал другой запрос и общее число не выросло)."""
    items, items_latest = (await db.execute(
        select(func.count(MarketplaceItem.id), func.max(func.coalesce(MarketplaceItem.submitted_at, MarketplaceItem.created_at)))
        .where(MarketplaceItem.moderation_status == PENDING, MarketplaceItem.is_active.is_(True),
               MarketplaceItem.type == "sell")
    )).one()
    apps, apps_latest = (await db.execute(
        select(func.count(ShopApplication.id), func.max(ShopApplication.id)).where(ShopApplication.status == "pending")
    )).one()
    reports, reports_latest = (await db.execute(
        select(func.count(Report.id), func.max(Report.id)).where(Report.status == "new")
    )).one()
    out = {
        "items": items or 0, "shop_applications": apps or 0, "reports": reports or 0,
        "latest": {"items": _epoch(items_latest), "shop_applications": apps_latest or 0, "reports": reports_latest or 0},
    }
    out["total"] = out["items"] + out["shop_applications"] + out["reports"]
    return out


# --- Карточка объявления для модератора ------------------------------------------------------------
def _short(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def _load_item(db: AsyncSession, item_id: int) -> MarketplaceItem | None:
    return (await db.execute(
        select(MarketplaceItem).options(*item_load_options()).where(MarketplaceItem.id == item_id)
    )).scalar_one_or_none()


def _card_text(item: MarketplaceItem, category: str | None) -> str:
    """Обычный текст без parse_mode: пользовательские данные не могут сломать разметку."""
    profile = item.user.profile if item.user else None
    username = profile.tg_username if profile else None
    if item.published_at is not None:
        head = "♻️ ПОВТОРНАЯ МОДЕРАЦИЯ — опубликованное объявление изменено"
    elif item.moderated_at is not None:
        head = "♻️ ПОВТОРНАЯ МОДЕРАЦИЯ — исправлено после отклонения"
    else:
        head = "🆕 НОВОЕ ОБЪЯВЛЕНИЕ НА МОДЕРАЦИИ"
    if item.shop:
        seller = f"Магазин «{item.shop.name}»"
    else:
        seller = "Частное лицо"
    seller += f" — @{username}" if username else ""
    seller += f" (ID {item.user.telegram_id})" if item.user else ""
    price = f"{item.price} {item.currency}" if item.price else "Договорная"
    city = (item.shop.city if item.shop else item.city) or "—"
    lines = [
        f"{head}",
        f"№{item.id}",
        "",
        f"📌 {_short(item.title, 120)}",
        f"💰 {price}",
        f"🗂 Категория: {category or '—'}",
    ]
    if item.brand:
        lines.append(f"🏷 Бренд: {item.brand}")
    lines += [f"📍 Город: {city}", f"👤 {seller}"]
    if len(item.photos) > 1:
        lines.append(f"📷 Фото: {len(item.photos)} (остальные — в панели модерации)")
    elif not item.photos:
        lines.append("📷 Без фото")
    if item.description:
        lines += ["", _short(item.description, 450)]
    return "\n".join(lines)


async def _build_card(item_id: int) -> tuple[str, bytes | None] | None:
    """Текст и первое фото карточки; None, если объявление уже не ждёт проверки."""
    async with async_session() as db:
        item = await _load_item(db, item_id)
        if not item or not item.is_active or item.moderation_status != PENDING:
            return None
        category = (await db.get(Category, item.category_id)).name if item.category_id else None
        text = _card_text(item, category)
        photo_path = item.photos[0].file_path if item.photos else None
    media = await read_media(photo_path) if photo_path else None
    return text, (media[0] if media else None)


async def send_item_card(chat_id: int, item_id: int, card: tuple[str, bytes | None] | None = None) -> bool:
    """Отправляет модератору карточку с кнопками. card можно передать готовую (для рассылки нескольким)."""
    card = card or await _build_card(item_id)
    if not card:
        return False
    text, photo = card
    markup = item_moderation_keyboard(item_id, WEBAPP_URL if WEBAPP_URL.startswith("https://") else None)
    try:
        if photo:
            # Лимит подписи к фото — 1024 символа.
            await bot.send_photo(chat_id, BufferedInputFile(photo, filename=f"item-{item_id}.jpg"),
                                 caption=_short(text, 1000), reply_markup=markup)
        else:
            await bot.send_message(chat_id, text, reply_markup=markup)
        return True
    except Exception as e:
        logger.warning("Cannot send moderation card to %s: %s", chat_id, e)
        return False


async def notify_admins_new_item(item_id: int) -> None:
    """Вызывается в фоне после создания/правки объявления: push-уведомление всем модераторам."""
    try:
        card = await _build_card(item_id)
        if not card:
            return
        for admin_id in await get_admin_ids():
            await send_item_card(admin_id, item_id, card)
            await asyncio.sleep(0.05)  # лимиты Telegram на рассылку
    except Exception:
        logger.exception("notify_admins_new_item failed for item %s", item_id)


# --- Решение модератора -------------------------------------------------------------------------------
async def approve_item(item_id: int, admin_tg_id: int, from_statuses=(PENDING, REJECTED)) -> tuple[str, str]:
    """Публикует объявление. Возвращает (код, сообщение). Атомарно: два модератора не одобрят одно дважды."""
    async with async_session() as db:
        item = await _load_item(db, item_id)
        if not item or not item.is_active:
            return NOT_FOUND, "Объявление не найдено или удалено продавцом"
        if item.moderation_status not in from_statuses:
            return CONFLICT, "Уже обработано другим модератором"
        now = datetime.now(timezone.utc)
        first_publication = item.published_at is None
        values = {
            "moderation_status": APPROVED, "reject_reason": None, "moderated_at": now, "moderated_by": admin_tg_id,
        }
        if first_publication:
            values["published_at"] = now
            values["bumped_at"] = now  # в ленте объявление считается свежим с момента публикации, а не подачи
        res = await db.execute(
            update(MarketplaceItem)
            .where(MarketplaceItem.id == item_id, MarketplaceItem.moderation_status == item.moderation_status)
            .values(**values)
        )
        if not res.rowcount:
            return CONFLICT, "Уже обработано другим модератором"
        await db.commit()
        owner_tg = item.user.telegram_id if item.user else None
        title, has_shop = item.title, item.shop_id is not None

    if owner_tg:
        await _safe_send(owner_tg, f"✅ Ваше объявление «{_short(title, 80)}» прошло модерацию и опубликовано.",
                         webapp_button("Открыть объявление", f"item={item_id}"))
    if first_publication:  # подписчикам — только о действительно новом объявлении, а не о каждой правке
        spawn(notify_saved_searches(item_id))
        if has_shop:
            spawn(notify_followers(item_id))
    return OK, "Объявление одобрено"


async def reject_item(item_id: int, admin_tg_id: int, reason: str | None,
                      from_statuses=(PENDING, APPROVED)) -> tuple[str, str]:
    """Отклоняет объявление (или снимает опубликованное). Продавец получает причину и может исправить."""
    reason = (reason or "").strip()[:500] or None
    async with async_session() as db:
        item = await _load_item(db, item_id)
        if not item or not item.is_active:
            return NOT_FOUND, "Объявление не найдено или удалено продавцом"
        if item.moderation_status not in from_statuses:
            return CONFLICT, "Уже обработано другим модератором"
        prev = item.moderation_status
        res = await db.execute(
            update(MarketplaceItem)
            .where(MarketplaceItem.id == item_id, MarketplaceItem.moderation_status == prev)
            .values(moderation_status=REJECTED, reject_reason=reason,
                    moderated_at=datetime.now(timezone.utc), moderated_by=admin_tg_id)
        )
        if not res.rowcount:
            return CONFLICT, "Уже обработано другим модератором"
        await db.commit()
        owner_tg = item.user.telegram_id if item.user else None
        title = item.title

    if owner_tg:
        what = "снято с публикации модератором" if prev == APPROVED else "не прошло модерацию"
        text = f"❌ Ваше объявление «{_short(title, 80)}» {what}."
        text += f"\nПричина: {reason}" if reason else ""
        text += "\n\nИсправьте его в разделе «Мои объявления» — после сохранения оно уйдёт на повторную проверку."
        await _safe_send(owner_tg, text, webapp_button("Мои объявления", "tab=myads"))
    return OK, "Объявление отклонено"
