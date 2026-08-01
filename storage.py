"""SQLite-хранилище подписок на новинки (watches).

watch_items — снапшот известных книг цели. Записи не удаляются (только растут),
чтобы флап выдачи не породил повторные «новинки». notified=0 + has_files=1 —
книга ждёт уведомления; notified ставится только после успешной отправки.
"""

import asyncio
import time
from dataclasses import dataclass

import aiosqlite

from providers.base import WatchEntry, WatchKind

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('series', 'author', 'query')),
    target TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    next_check_at INTEGER NOT NULL,
    last_ok_at INTEGER,
    failures INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, kind, target)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_items (
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    book_id TEXT NOT NULL,
    title TEXT NOT NULL,
    has_files INTEGER NOT NULL,
    notified INTEGER NOT NULL,
    first_seen_at INTEGER NOT NULL,
    PRIMARY KEY (watch_id, book_id)
);
"""


@dataclass(slots=True)
class Watch:
    id: int
    user_id: int
    chat_id: int
    kind: WatchKind
    target: str
    label: str
    failures: int


def _row_to_watch(row: aiosqlite.Row) -> Watch:
    return Watch(
        id=row["id"],
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        kind=row["kind"],
        target=row["target"],
        label=row["label"],
        failures=row["failures"],
    )


class Storage:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db
        # Одно соединение на watcher и handlers: commit() фиксирует всё накопленное,
        # поэтому многошаговые записи сериализуем, чтобы не закоммитить чужую половину
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str) -> "Storage":
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(_SCHEMA)
        await db.commit()
        return cls(db)

    async def close(self) -> None:
        await self._db.close()

    # ------------------------------------------------------------------- meta

    async def get_meta(self, key: str) -> str | None:
        cur = await self._db.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self._write_lock:
            await self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await self._db.commit()

    # ---------------------------------------------------------------- watches

    async def add_watch(
        self,
        user_id: int,
        chat_id: int,
        kind: WatchKind,
        target: str,
        label: str,
        baseline: list[WatchEntry],
        next_check_at: int,
    ) -> int | None:
        """Создаёт подписку с baseline-снапшотом. None — такая подписка уже есть.

        Baseline-книги без файлов остаются notified=0: когда файлы появятся —
        придёт уведомление (кейс «карточка есть, скачать пока нечего»)."""
        now = int(time.time())
        async with self._write_lock:
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO watches (user_id, chat_id, kind, target, label, created_at, next_check_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, chat_id, kind, target, label, now, next_check_at),
            )
            if cur.rowcount == 0:
                return None
            watch_id = cur.lastrowid
            await self._db.executemany(
                "INSERT OR IGNORE INTO watch_items (watch_id, book_id, title, has_files, notified, first_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(watch_id, e.book_id, e.title, int(e.has_files), int(e.has_files), now) for e in baseline],
            )
            await self._db.commit()
        return watch_id

    async def list_watches(self, user_id: int) -> list[Watch]:
        cur = await self._db.execute(
            "SELECT * FROM watches WHERE user_id = ? ORDER BY created_at", (user_id,)
        )
        return [_row_to_watch(r) for r in await cur.fetchall()]

    async def delete_watch(self, watch_id: int, user_id: int) -> bool:
        async with self._write_lock:
            cur = await self._db.execute(
                "DELETE FROM watches WHERE id = ? AND user_id = ?", (watch_id, user_id)
            )
            await self._db.commit()
        return cur.rowcount > 0

    async def due_watches(self, now: int) -> list[Watch]:
        cur = await self._db.execute(
            "SELECT * FROM watches WHERE next_check_at <= ? ORDER BY next_check_at", (now,)
        )
        return [_row_to_watch(r) for r in await cur.fetchall()]

    async def record_check_ok(self, watch_id: int, next_check_at: int) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE watches SET failures = 0, last_ok_at = ?, next_check_at = ? WHERE id = ?",
                (int(time.time()), next_check_at, watch_id),
            )
            await self._db.commit()

    async def record_check_fail(self, watch_id: int, next_check_at: int) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE watches SET failures = failures + 1, next_check_at = ? WHERE id = ?",
                (next_check_at, watch_id),
            )
            await self._db.commit()

    # ------------------------------------------------------------ watch_items

    async def known_book_ids(self, watch_id: int) -> set[str]:
        cur = await self._db.execute(
            "SELECT book_id FROM watch_items WHERE watch_id = ?", (watch_id,)
        )
        return {r["book_id"] for r in await cur.fetchall()}

    async def upsert_items(self, watch_id: int, entries: list[WatchEntry]) -> None:
        """Новые книги — notified=0; у известных обновляется только has_files (False→True)."""
        now = int(time.time())
        async with self._write_lock:
            for e in entries:
                await self._db.execute(
                    "INSERT INTO watch_items (watch_id, book_id, title, has_files, notified, first_seen_at)"
                    " VALUES (?, ?, ?, ?, 0, ?)"
                    " ON CONFLICT (watch_id, book_id) DO UPDATE SET has_files = max(has_files, excluded.has_files)",
                    (watch_id, e.book_id, e.title, int(e.has_files), now),
                )
            await self._db.commit()

    async def pending_notifications(self, watch_id: int) -> list[tuple[str, str]]:
        """[(book_id, title)] — обнаружены, файлы доступны, ещё не уведомляли."""
        cur = await self._db.execute(
            "SELECT book_id, title FROM watch_items"
            " WHERE watch_id = ? AND notified = 0 AND has_files = 1 ORDER BY first_seen_at",
            (watch_id,),
        )
        return [(r["book_id"], r["title"]) for r in await cur.fetchall()]

    async def mark_notified(self, watch_id: int, book_ids: list[str]) -> None:
        async with self._write_lock:
            await self._db.executemany(
                "UPDATE watch_items SET notified = 1 WHERE watch_id = ? AND book_id = ?",
                [(watch_id, b) for b in book_ids],
            )
            await self._db.commit()
