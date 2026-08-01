"""Фоновая проверка подписок: OPDS-снапшот цели → дифф с watch_items → уведомления.

Запускается рядом с polling'ом под TaskGroup: ошибки одной проверки гасятся и
уводятся в backoff, а неожиданное падение цикла роняет процесс целиком — его
перезапустит systemd/docker.
"""

import asyncio
import random
import sqlite3
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger

from bot.formatters import escape, format_book
from bot.keyboards import book_formats_kb
from config import settings
from providers.base import BookProvider, ProviderError, WatchSource
from storage import Storage, Watch

_TICK = 60  # период опроса БД на предмет due-подписок
_BETWEEN_TARGETS = 3.0  # пауза между целями: один IP, не долбим флибусту
_BETWEEN_SENDS = 1.0
_MAX_FAIL_BACKOFF = 24 * 3600
# Больше новинок за цикл — шлём одно сводное сообщение вместо потока карточек
# (заодно карантин от шторма при смене семантики выдачи)
_MANY_NEW = 8

_KIND_GENITIVE = {"series": "серии", "author": "автора", "query": "запросу"}


class Watcher:
    def __init__(self, bot: Bot, provider: BookProvider, storage: Storage) -> None:
        assert isinstance(provider, WatchSource)
        self._bot = bot
        self._provider = provider
        self._source: WatchSource = provider
        self._storage = storage

    async def run(self) -> None:
        logger.info("Watcher started (interval ~{}h)", settings.watch_interval_hours)
        while True:
            await self._tick()
            await asyncio.sleep(_TICK)

    async def _tick(self) -> None:
        due = await self._storage.due_watches(int(time.time()))
        for i, watch in enumerate(due):
            if i:
                await asyncio.sleep(_BETWEEN_TARGETS)
            await self._check(watch)

    async def _check(self, watch: Watch) -> None:
        base = settings.watch_interval_hours * 3600
        try:
            snapshot = await self._source.watch_entries(watch.kind, watch.target)
            if not snapshot.complete:
                raise ProviderError("snapshot incomplete (page cap)")
            if not snapshot.entries and await self._storage.known_book_ids(watch.id):
                # Пустая выдача при непустом снапшоте — скорее поломка, чем «всё удалили»
                raise ProviderError("snapshot suddenly empty")
        except ProviderError as e:
            delay = min(base * 2 ** (watch.failures + 1), _MAX_FAIL_BACKOFF)
            logger.warning("watch #{} «{}»: {} — retry in {:.0f}m", watch.id, watch.label, e, delay / 60)
            await self._storage.record_check_fail(watch.id, int(time.time() + delay))
            return

        try:
            await self._storage.upsert_items(watch.id, snapshot.entries)
            await self._notify(watch)
        except sqlite3.IntegrityError:
            # FK на watches: подписку удалили, пока шёл OPDS-запрос — штатная отмена
            logger.info("watch #{} «{}» deleted during check", watch.id, watch.label)
            return
        next_at = int(time.time() + base * (0.9 + 0.2 * random.random()))
        await self._storage.record_check_ok(watch.id, next_at)

    async def _notify(self, watch: Watch) -> None:
        pending = await self._storage.pending_notifications(watch.id)
        if not pending:
            return
        header = f"🔔 Новинка по {_KIND_GENITIVE[watch.kind]} «{escape(watch.label)}»"
        logger.info("watch #{} «{}»: {} new book(s)", watch.id, watch.label, len(pending))

        if len(pending) > _MANY_NEW:
            # 20 строк с обрезанными названиями — гарантированно внутри лимита 4096
            lines = "\n".join(
                f'• <a href="{self._provider.book_url(bid)}">{escape(title[:60])}</a>'
                for bid, title in pending[:20]
            )
            extra = f"\n…и ещё {len(pending) - 20}" if len(pending) > 20 else ""
            try:
                await self._bot.send_message(
                    watch.chat_id,
                    f"{header} — сразу {len(pending)} шт.:\n{lines}{extra}",
                    disable_web_page_preview=True,
                )
            except TelegramAPIError as e:
                logger.warning("watch #{}: send failed: {} — will retry", watch.id, e)
                return
            await self._storage.mark_notified(watch.id, [bid for bid, _ in pending])
            return

        for book_id, title in pending:
            text, kb = await self._book_card(header, book_id, title)
            try:
                await self._bot.send_message(watch.chat_id, text, reply_markup=kb)
            except TelegramAPIError as e:
                logger.warning("watch #{}: send failed: {} — will retry", watch.id, e)
                return  # notified не ставим — повторим в следующем цикле
            await self._storage.mark_notified(watch.id, [book_id])
            await asyncio.sleep(_BETWEEN_SENDS)

    async def _book_card(self, header: str, book_id: str, title: str):
        try:
            book = await self._provider.get_book(book_id)
        except ProviderError as e:
            logger.warning("get_book {} for notification: {}", book_id, e)
            url = self._provider.book_url(book_id)
            return f"{header}\n\n<b>{escape(title)}</b>\n{url}", None
        text = f"{header}\n\n{format_book(book)}"
        if book.downloads:
            text += "\n\n<b>Скачать:</b>"
            return text, book_formats_kb(book)
        return text, None
