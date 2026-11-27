import logging
from aiogram import Router, F, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from bot.keyboards import main_menu_keyboard, admin_order_keyboard
from bot.states import AdminStates
from database import async_session
from models import MarketplaceItem, Order, User, Message as OrderMessage, Review
from services.admin_service import is_admin_tg
from services.moderation_service import (
    CONFLICT, OK, PENDING, approve_item, pending_counts, reject_item, send_item_card,
)
from services.notification_service import (
    notify_client_price, notify_client_message, notify_client_stage,
)
from services.stage_service import get_all_stages, get_next_stage, get_prev_stage, get_rejected_stage
from services.telegram_service import bot

logger = logging.getLogger(__name__)
router = Router()

def _stage_label(order):
    return order.current_stage.label if order.current_stage else "—"

async def _open_from_link(message: Message, kind: str, obj_id: int) -> bool:
    """/start item_5 | shop_3 (ссылка «Поделиться»): отвечаем карточкой и кнопкой, открывающей нужную страницу."""
    from models import Shop
    from services.saved_search_service import webapp_button
    async with async_session() as db:
        if kind == "item":
            item = (await db.execute(
                select(MarketplaceItem).options(selectinload(MarketplaceItem.shop)).where(MarketplaceItem.id == obj_id)
            )).scalar_one_or_none()
            ok = bool(item and item.is_active and item.moderation_status == "approved"
                      and not (item.shop and item.shop.status != "active"))
            if ok:
                price = f"{item.price} {item.currency}" if item.price else "Цена договорная"
                text, label, query = f"🛍 {item.title}\n{price}", "Открыть объявление", f"item={obj_id}"
        else:
            shop = await db.get(Shop, obj_id)
            ok = bool(shop and shop.status == "active")
            if ok:
                text = f"🏬 {shop.name}" + (f"\n📍 {shop.city}" if shop.city else "")
                label, query = "Открыть магазин", f"shop={obj_id}"
    if not ok:
        await message.answer("Эта страница недоступна: возможно, её удалили или скрыли.")
        return False
    markup = webapp_button(label, query)
    if markup is None:  # WEBAPP_URL без https: кнопку Telegram не примет
        await message.answer(text + "\n\nОткройте маркет через меню бота.")
        return True
    await message.answer(text, reply_markup=markup)
    return True


@router.message(Command("start"))
async def start(message: Message, command: CommandObject):
    import re
    m = re.fullmatch(r"(item|shop)_(\d{1,12})", (command.args or "").strip())
    if m and await _open_from_link(message, m.group(1), int(m.group(2))):
        return
    is_admin = await is_admin_tg(message.from_user.id)
    await message.answer(
        "Добро пожаловать в Orient! Откройте маркет в мини-приложении.",
        reply_markup=main_menu_keyboard(is_admin)
    )

@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Помощь:\n"
        "/start — главное меню\n"
        "/myorders — мои заказы\n"
        "/admin — панель управления и очередь модерации\n"
        "/queue — объявления, ожидающие проверки\n"
        "/orders — список заказов\n"
        "/reply <id> <текст> — ответ клиенту"
    )

@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменено.")


@router.message(Command("myorders"))
async def my_orders(message: Message):
    async with async_session() as db:
        result = await db.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("У вас пока нет заказов.")
            return
        result = await db.execute(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()))
        orders = result.scalars().all()
        if not orders:
            await message.answer("У вас пока нет заказов.")
            return
        text = "Ваши заказы:\n\n" + "\n".join(
            f"#{o.id} — {_stage_label(o)} — {o.price or 'цена не указана'}" for o in orders
        )
        await message.answer(text)

@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not await is_admin_tg(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    async with async_session() as db:
        c = await pending_counts(db)
    await message.answer(
        "Админ-панель\n\n"
        f"🆕 Объявлений на модерации: {c['items']}\n"
        f"🏬 Заявок на магазины: {c['shop_applications']}\n"
        f"🚩 Новых жалоб: {c['reports']}\n\n"
        "/queue — показать объявления, ожидающие проверки",
        reply_markup=main_menu_keyboard(is_admin=True),
    )

QUEUE_BATCH = 5


@router.message(Command("queue"))
async def queue_cmd(message: Message):
    if not await is_admin_tg(message.from_user.id):
        return
    async with async_session() as db:
        ids = (await db.execute(
            select(MarketplaceItem.id)
            .where(MarketplaceItem.moderation_status == PENDING, MarketplaceItem.is_active.is_(True))
            .order_by(MarketplaceItem.id.asc())
        )).scalars().all()
    if not ids:
        await message.answer("✅ Очередь пуста — все объявления проверены.")
        return
    for item_id in ids[:QUEUE_BATCH]:
        await send_item_card(message.chat.id, item_id)
    if len(ids) > QUEUE_BATCH:
        await message.answer(f"Показаны первые {QUEUE_BATCH} из {len(ids)}. Остальные — в панели модерации или после /queue.")


@router.message(Command("orders"))
async def orders_cmd(message: Message):
    if not await is_admin_tg(message.from_user.id):
        return
    async with async_session() as db:
        result = await db.execute(select(Order).order_by(Order.created_at.desc()).limit(20))
        orders = result.scalars().all()
        if not orders:
            await message.answer("Нет заказов.")
            return
        for o in orders:
            await message.answer(
                f"#{o.id} — {_stage_label(o)} — {o.price or '—'}",
                reply_markup=admin_order_keyboard(o.id)
            )

@router.message(Command("reply"))
async def reply_cmd(message: Message):
    if not await is_admin_tg(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Использование: /reply <order_id> <текст>")
        return
    order_id = int(parts[1])
    text = parts[2]
    async with async_session() as db:
        order = await db.get(Order, order_id)
        if not order:
            await message.answer("Заказ не найден")
            return
        user = await db.get(User, order.user_id)
        msg = OrderMessage(order_id=order.id, sender_id=message.from_user.id, message_text=text, message_type="text")
        db.add(msg)
        await db.commit()
        await notify_client_message(order, user, text, prefix="💬 Ответ по заказу")
    await message.answer("Отправлено клиенту.")

@router.callback_query(F.data.startswith("order:"))
async def admin_order_action(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_tg(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1]
    order_id = int(parts[2])
    if action == "price":
        await state.set_state(AdminStates.waiting_price)
        await state.update_data(order_id=order_id)
        await callback.message.answer("Введите стоимость заказа:")
    elif action == "reply":
        await state.set_state(AdminStates.waiting_reply)
        await state.update_data(order_id=order_id)
        await callback.message.answer("Введите сообщение для клиента:")
    elif action == "question":
        await state.set_state(AdminStates.waiting_question)
        await state.update_data(order_id=order_id)
        await callback.message.answer("Введите вопрос для клиента:")
    elif action == "info":
        async with async_session() as db:
            result = await db.execute(
                select(Order).options(selectinload(Order.messages), selectinload(Order.current_stage)).where(Order.id == order_id)
            )
            order = result.scalar_one_or_none()
            if order:
                user = await db.get(User, order.user_id)
                history = "\n".join(
                    f"{'👤' if m.sender_id == user.telegram_id else '👨‍💼'} {m.message_text}"
                    for m in order.messages[-10:]
                ) or "—"
                await callback.message.answer(
                    f"Заказ #{order.id}\n"
                    f"Клиент: {user.first_name or '—'} @{user.username or '—'}\n"
                    f"Телефон: {order.phone or '—'}\n"
                    f"Email: {order.email or '—'}\n"
                    f"Адрес: {order.delivery_address or '—'}\n"
                    f"Промокод: {order.promo_code or '—'}\n"
                    f"Описание: {order.description}\n"
                    f"Комментарий: {order.additional_comment or '—'}\n"
                    f"Этап: {_stage_label(order)}\n"
                    f"Цена: {order.price or '—'}\n\n"
                    f"История:\n{history}"
                )
    await callback.answer()

@router.callback_query(F.data.startswith("stage:"))
async def stage_action(callback: CallbackQuery):
    if not await is_admin_tg(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1]
    if action == "menu":
        async with async_session() as db:
            stages = await get_all_stages(db)
            text = "Этапы заказа:\n\n" + "\n".join(
                f"{s.position}. {s.label}{' (финал)' if s.is_final else ''}{' (отклонён)' if s.is_rejected else ''}"
                for s in stages
            )
            await callback.message.answer(text + "\n\nУправление — в админ-панели.")
        await callback.answer()
        return
    order_id = int(parts[2])
    async with async_session() as db:
        order = await db.get(Order, order_id)
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        user = await db.get(User, order.user_id)
        if action == "next":
            stage = await get_next_stage(db, order.current_stage_id)
            if not stage:
                await callback.answer("Уже последний этап", show_alert=True)
                return
        elif action == "prev":
            stage = await get_prev_stage(db, order.current_stage_id)
            if not stage:
                await callback.answer("Уже первый этап", show_alert=True)
                return
        elif action == "complete":
            stages = await get_all_stages(db)
            stage = next((s for s in stages if s.is_final and not s.is_rejected), None)
        elif action == "reject":
            stage = await get_rejected_stage(db)
        else:
            await callback.answer()
            return
        if not stage:
            await callback.answer("Этап не настроен", show_alert=True)
            return
        order.current_stage_id = stage.id
        if stage.is_rejected:
            order.status = "rejected"
        elif stage.is_final:
            order.status = "completed"
        else:
            order.status = "active"
        await db.commit()
        await notify_client_stage(order, user, stage)
        await callback.message.answer(f"Заказ #{order_id}: {stage.label}")
    await callback.answer()

@router.callback_query(F.data.startswith("review:"))
async def review_action(callback: CallbackQuery):
    if not await is_admin_tg(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1]
    review_id = int(parts[2])
    async with async_session() as db:
        review = await db.get(Review, review_id)
        if not review:
            await callback.answer("Отзыв не найден", show_alert=True)
            return
        review.status = "published" if action == "publish" else "hidden"
        await db.commit()
    await callback.message.answer(f"Отзыв #{review_id}: {'опубликован' if action == 'publish' else 'скрыт'}")
    await callback.answer()

@router.message(AdminStates.waiting_price, F.text)
async def process_price(message: Message, state: FSMContext):
    if not await is_admin_tg(message.from_user.id):
        return
    data = await state.get_data()
    order_id = data["order_id"]
    price = message.text.strip()
    async with async_session() as db:
        order = await db.get(Order, order_id)
        if not order:
            await message.answer("Заказ не найден")
            await state.clear()
            return
        order.price = price
        await db.commit()
        user = await db.get(User, order.user_id)
        await notify_client_price(order, user)
    await message.answer(f"Цена {price} отправлена клиенту.")
    await state.clear()

@router.message(AdminStates.waiting_reply, F.text)
async def process_reply(message: Message, state: FSMContext):
    if not await is_admin_tg(message.from_user.id):
        return
    data = await state.get_data()
    order_id = data["order_id"]
    text = message.text.strip()
    async with async_session() as db:
        order = await db.get(Order, order_id)
        if not order:
            await message.answer("Заказ не найден")
            await state.clear()
            return
        user = await db.get(User, order.user_id)
        msg = OrderMessage(order_id=order.id, sender_id=message.from_user.id, message_text=text, message_type="text")
        db.add(msg)
        await db.commit()
        await notify_client_message(order, user, text)
    await message.answer("Сообщение отправлено клиенту.")
    await state.clear()

@router.message(AdminStates.waiting_question, F.text)
async def process_question(message: Message, state: FSMContext):
    if not await is_admin_tg(message.from_user.id):
        return
    data = await state.get_data()
    order_id = data["order_id"]
    text = message.text.strip()
    async with async_session() as db:
        order = await db.get(Order, order_id)
        if not order:
            await message.answer("Заказ не найден")
            await state.clear()
            return
        user = await db.get(User, order.user_id)
        msg = OrderMessage(order_id=order.id, sender_id=message.from_user.id, message_text=text, message_type="question")
        db.add(msg)
        await db.commit()
        await notify_client_message(order, user, f"❓ {text}", prefix="Вопрос по заказу")
    await message.answer("Вопрос отправлен клиенту.")
    await state.clear()

# --- МОДЕРАЦИЯ ОБЪЯВЛЕНИЙ ---------------------------------------------------------------------------
async def _close_card(chat_id: int, message_id: int) -> None:
    """Убирает кнопки у обработанной карточки, чтобы их нельзя было нажать повторно."""
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:  # сообщение удалено / уже без кнопок
        logger.debug("Cannot close moderation card %s/%s", chat_id, message_id)


@router.callback_query(F.data.startswith("mod:"))
async def moderation_action(callback: CallbackQuery, state: FSMContext):
    if not await is_admin_tg(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("approve", "reject") or not parts[2].isdigit():
        await callback.answer()
        return
    action, item_id = parts[1], int(parts[2])
    msg = callback.message if isinstance(callback.message, Message) else None
    if action == "approve":
        # Из бота действуем только по ожидающим проверки: устаревшая карточка не должна менять чужое решение.
        code, text = await approve_item(item_id, callback.from_user.id, from_statuses=(PENDING,))
        await callback.answer(("✅ Одобрено и опубликовано" if code == OK else text), show_alert=code != OK)
        if msg:
            await _close_card(msg.chat.id, msg.message_id)
        return
    # reject: просим причину отдельным сообщением
    if msg is None:
        await callback.answer("Откройте карточку заново через /queue", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_item_reject_reason)
    await state.update_data(item_id=item_id, card_chat_id=msg.chat.id, card_message_id=msg.message_id)
    await callback.message.answer(
        f"Укажите причину отклонения объявления №{item_id} одним сообщением — её получит продавец.\n"
        "«-» — отклонить без причины, /cancel — отмена."
    )
    await callback.answer()


@router.message(AdminStates.waiting_item_reject_reason, F.text)
async def process_item_reject(message: Message, state: FSMContext):
    if not await is_admin_tg(message.from_user.id):
        return
    data = await state.get_data()
    await state.clear()
    item_id = data.get("item_id")
    if not item_id:
        return
    reason = message.text.strip()
    code, text = await reject_item(item_id, message.from_user.id, None if reason in ("-", "—") else reason,
                                   from_statuses=(PENDING,))
    if code == OK:
        await message.answer(f"❌ Объявление №{item_id} отклонено, продавец уведомлён.")
    else:
        await message.answer(text)
    if code in (OK, CONFLICT) and data.get("card_chat_id") and data.get("card_message_id"):
        await _close_card(data["card_chat_id"], data["card_message_id"])


dp = Dispatcher()
dp.include_router(router)