"""Разовый анонс после деплоя: уходит всем авторизованным, версия помечается в SQLite.

Новый анонс = новый текст + новое значение ANNOUNCE_VERSION. Метка ставится после
рассылки целиком: упавший на середине процесс продолжит с начала (возможен дубль
у части пользователей — приемлемо при нашем масштабе, потери нет).
"""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger

from bot import auth
from storage import Storage

_META_KEY = "announced_version"

ANNOUNCE_VERSION = "2026-08-01-watches"

ANNOUNCE_TEXT = (
    "<b>Обновление: подписки на новинки</b>\n\n"
    "Теперь не нужно каждый день вбивать поиск и проверять, вышла ли книга — "
    "бот пришлёт уведомление сам. Три вида подписок:\n\n"
    "📚 <b>Серия</b> — кнопка «🔔 Следить за серией …» на карточке книги. "
    "Идеально, когда ждёте следующий том цикла.\n"
    "✍️ <b>Автор</b> — кнопка «🔔 Следить за автором» там же. Любая новая книга автора.\n"
    "🔎 <b>Запрос</b> — кнопка «🔔 Следить за новинками по этому запросу» под результатами поиска. "
    "Работает, даже если книги на Флибусте ещё нет совсем.\n\n"
    "Запрос ищет по названию книги. Подписка «Гарри Поттер» поймает будущую "
    "«Гарри Поттер и новое проклятие», а вот подписка «Гарри Поттер и Кубок огня» "
    "новую книгу с другим названием не найдёт. Поэтому лучше короткий запрос, серия или автор.\n\n"
    "Уведомление придёт карточкой книги с кнопками скачивания — качайте прямо из него.\n\n"
    "Все подписки: /watches (там же можно и удалить)."
)


async def send_pending_announcement(bot: Bot, storage: Storage) -> None:
    if await storage.get_meta(_META_KEY) == ANNOUNCE_VERSION:
        return
    users = auth.all_users()
    sent = 0
    for user_id in sorted(users):
        try:
            await bot.send_message(user_id, ANNOUNCE_TEXT, parse_mode="HTML")
            sent += 1
        except TelegramAPIError as e:
            # Пользователь заблокировал бота и т.п. — не повод не уведомить остальных
            logger.warning("announce to {}: {}", user_id, e)
        await asyncio.sleep(0.2)
    await storage.set_meta(_META_KEY, ANNOUNCE_VERSION)
    logger.info("Announcement {!r} sent to {}/{} user(s)", ANNOUNCE_VERSION, sent, len(users))
