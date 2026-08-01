"""Подписки: OPDS-парсинг, диффы в storage, цикл watcher'а (httpx.MockTransport, без сети)."""

import time

import httpx
import pytest
from aiogram.exceptions import TelegramAPIError

import providers.flibusta as fl
import watcher as watcher_mod
from providers.base import ProviderError, WatchEntry, WatchSnapshot, WatchSource
from storage import Storage
from watcher import Watcher

FEED_PAGE1 = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Книги в серии Тест</title>
<link href="/opds/sequencebooks/1/next" rel="next" type="application/atom+xml"/>
<entry><title>Том 1</title><author><name>Автор А</name></author>
<link href="/b/100/fb2" rel="http://opds-spec.org/acquisition/open-access" type="application/fb2+zip"/>
<link href="/b/100" rel="alternate" type="text/html"/></entry>
</feed>"""

FEED_PAGE2 = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Книги в серии Тест</title>
<entry><title>Том 2 [СИ]</title><author><name>Автор А</name></author>
<link href="/b/200" rel="alternate" type="text/html"/></entry>
</feed>"""

FEED_SELF_LOOP = FEED_PAGE1.replace("/opds/sequencebooks/1/next", "/opds/sequencebooks/1")


def opds_provider(pages: dict[str, str]) -> fl.FlibustaProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in pages:
            return httpx.Response(200, text=pages[path])
        return httpx.Response(404)

    return fl.FlibustaProvider(transport=httpx.MockTransport(handler))


async def test_watch_entries_paginated():
    p = opds_provider({"/opds/sequencebooks/1": FEED_PAGE1, "/opds/sequencebooks/1/next": FEED_PAGE2})
    snap = await p.watch_entries("series", "1")
    assert snap.label == "Тест"
    assert snap.complete
    assert [(e.book_id, e.has_files) for e in snap.entries] == [("100", True), ("200", False)]


async def test_watch_entries_pagination_loop_raises():
    p = opds_provider({"/opds/sequencebooks/1": FEED_SELF_LOOP})
    with pytest.raises(ProviderError, match="loop"):
        await p.watch_entries("series", "1")


async def test_watch_entries_rejects_non_feed():
    p = opds_provider({"/opds/sequencebooks/1": "<html>Ой, капча</html>"})
    with pytest.raises(ProviderError):
        await p.watch_entries("series", "1")


# ------------------------------------------------------------------- storage


async def test_storage_diff_and_notify_flow(tmp_path):
    st = await Storage.open(str(tmp_path / "t.db"))
    baseline = [WatchEntry("100", "Том 1", has_files=True), WatchEntry("150", "Том 1.5", has_files=False)]
    wid = await st.add_watch(1, 1, "series", "1", "Тест", baseline, next_check_at=0)
    assert wid is not None
    # дубль не создаётся
    assert await st.add_watch(1, 1, "series", "1", "Тест", [], next_check_at=0) is None
    # baseline: книга с файлами уже notified, без файлов — ждёт появления файлов
    assert await st.pending_notifications(wid) == []

    # у baseline-книги появились файлы + пришла новая
    await st.upsert_items(wid, [WatchEntry("150", "Том 1.5", has_files=True), WatchEntry("300", "Том 3", has_files=True)])
    assert await st.pending_notifications(wid) == [("150", "Том 1.5"), ("300", "Том 3")]
    await st.mark_notified(wid, ["150", "300"])
    assert await st.pending_notifications(wid) == []
    assert await st.known_book_ids(wid) == {"100", "150", "300"}

    # удаление подписки чистит items (FK cascade)
    assert await st.delete_watch(wid, user_id=1)
    assert await st.known_book_ids(wid) == set()
    await st.close()


# ------------------------------------------------------------------- watcher


class FakeProvider(WatchSource):
    def __init__(self) -> None:
        self.snapshot = WatchSnapshot(label="Тест", entries=[], complete=True)
        self.fail = False

    async def watch_entries(self, kind, target):
        if self.fail:
            raise ProviderError("boom")
        return self.snapshot

    async def get_book(self, book_id):
        raise ProviderError("no card")  # уведомление уходит fallback-текстом

    def book_url(self, book_id):
        return f"https://x/b/{book_id}"


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = False

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_next:
            self.fail_next = False
            raise TelegramAPIError(method=None, message="tg down")
        self.sent.append(text)


async def make_watcher(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_mod, "_BETWEEN_SENDS", 0)
    st = await Storage.open(str(tmp_path / "w.db"))
    provider = FakeProvider()
    bot = FakeBot()
    w = Watcher(bot, provider, st)
    return w, st, provider, bot


async def only_watch(st):
    return (await st.due_watches(int(time.time()) + 10**9))[0]


async def test_watcher_notifies_once(tmp_path, monkeypatch):
    w, st, provider, bot = await make_watcher(tmp_path, monkeypatch)
    await st.add_watch(1, 42, "series", "1", "Тест", [WatchEntry("100", "Том 1", has_files=True)], 0)
    provider.snapshot = WatchSnapshot(
        label="Тест",
        entries=[WatchEntry("100", "Том 1", has_files=True), WatchEntry("200", "Том 2", has_files=True)],
    )
    await w._check(await only_watch(st))
    assert len(bot.sent) == 1 and "Том 2" in bot.sent[0]
    # повторный цикл — без дублей
    await w._check(await only_watch(st))
    assert len(bot.sent) == 1
    await st.close()


async def test_watcher_send_failure_retries_later(tmp_path, monkeypatch):
    w, st, provider, bot = await make_watcher(tmp_path, monkeypatch)
    await st.add_watch(1, 42, "series", "1", "Тест", [], 0)
    provider.snapshot = WatchSnapshot(label="Тест", entries=[WatchEntry("200", "Том 2", has_files=True)])
    bot.fail_next = True
    await w._check(await only_watch(st))
    assert bot.sent == []  # отправка упала → notified не выставлен
    await w._check(await only_watch(st))
    assert len(bot.sent) == 1  # следующий цикл дослал
    await st.close()


async def test_watcher_survives_delete_during_check(tmp_path, monkeypatch):
    w, st, provider, bot = await make_watcher(tmp_path, monkeypatch)
    wid = await st.add_watch(1, 42, "series", "1", "Тест", [], 0)
    watch = await only_watch(st)

    async def delete_then_return(kind, target):
        await st.delete_watch(wid, user_id=1)
        return WatchSnapshot(label="Тест", entries=[WatchEntry("200", "Том 2", has_files=True)])

    provider.watch_entries = delete_then_return
    await w._check(watch)  # FK-ошибка гасится как штатная отмена, watcher жив
    assert bot.sent == []
    await st.close()


async def test_announcement_sent_once(tmp_path, monkeypatch):
    from bot import announce, auth

    monkeypatch.setattr(auth, "_authorized", {1, 2})
    st = await Storage.open(str(tmp_path / "a.db"))
    bot = FakeBot()
    bot.fail_next = True  # у первого юзера отправка падает — второй всё равно получает
    await announce.send_pending_announcement(bot, st)
    assert len(bot.sent) == 1
    await announce.send_pending_announcement(bot, st)
    assert len(bot.sent) == 1  # версия помечена — повторной рассылки при рестарте нет
    await st.close()


async def test_watcher_error_and_empty_snapshot_backoff(tmp_path, monkeypatch):
    w, st, provider, bot = await make_watcher(tmp_path, monkeypatch)
    await st.add_watch(1, 42, "series", "1", "Тест", [WatchEntry("100", "Том 1", has_files=True)], 0)

    provider.fail = True
    await w._check(await only_watch(st))
    watch = await only_watch(st)
    assert watch.failures == 1 and bot.sent == []

    # пустая выдача при непустом снапшоте — тоже ошибка, известные книги не трогаем
    provider.fail = False
    provider.snapshot = WatchSnapshot(label="Тест", entries=[])
    await w._check(await only_watch(st))
    assert (await only_watch(st)).failures == 2
    assert await st.known_book_ids(watch.id) == {"100"}
    await st.close()
