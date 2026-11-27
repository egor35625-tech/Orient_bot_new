"""Кто является модератором: ADMIN_IDS из .env плюс админы, добавленные через панель (таблица admin_users).

Раньше бот, старые заказы и уведомления учитывали только ADMIN_IDS, поэтому добавленные через API админы
пускались в веб-панель, но не получали уведомлений и не могли пользоваться кнопками бота.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_IDS
from database import async_session
from models import AdminUser


async def get_admin_ids(db: AsyncSession | None = None) -> set[int]:
    """Все модераторы. Можно передать открытую сессию, иначе будет открыта новая."""
    async def _load(session: AsyncSession) -> set[int]:
        rows = (await session.execute(select(AdminUser.telegram_id))).scalars().all()
        return set(ADMIN_IDS) | set(rows)

    if db is not None:
        return await _load(db)
    async with async_session() as session:
        return await _load(session)


async def is_admin_tg(tg_id: int | None, db: AsyncSession | None = None) -> bool:
    if not tg_id:
        return False
    if tg_id in ADMIN_IDS:  # быстрый путь без обращения к БД
        return True
    async def _check(session: AsyncSession) -> bool:
        row = await session.execute(select(AdminUser.id).where(AdminUser.telegram_id == tg_id))
        return row.first() is not None

    if db is not None:
        return await _check(db)
    async with async_session() as session:
        return await _check(session)
