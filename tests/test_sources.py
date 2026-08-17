"""Развязка на два источника: бюджет callback'ов, устаревшие кнопки, границы способностей."""

import httpx
import pytest

from bot.callback_data import LIBGEN, BookCb, DownloadCb, PageCb, SrcCb
from providers.base import WatchSource
from providers.flibusta import FlibustaProvider
from providers.libgen import LibgenProvider

MD5 = "49bad0ead365a6b798fe64c1a1d432aa"


def _mock(status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status))


def test_libgen_callbacks_fit_telegram_limit():
    """md5 (32 символа) сильно длиннее числового id Флибусты — 64-байтный лимит надо стеречь."""
    packed = [
        BookCb(src=LIBGEN, id=MD5).pack(),
        DownloadCb(src=LIBGEN, book_id=MD5, fmt="download").pack(),
        PageCb(page=999, kind="search", target_id="", src=LIBGEN).pack(),
        SrcCb(src=LIBGEN).pack(),
    ]
    assert all(len(p.encode()) <= 64 for p in packed), packed


@pytest.mark.parametrize(
    ("cls", "legacy"),
    [(BookCb, "b:12345"), (DownloadCb, "dl:12345:fb2"), (PageCb, "pg:2:search:")],
)
def test_legacy_callbacks_no_longer_unpack(cls, legacy):
    """Кнопки, отправленные до появления поля `src`.

    aiogram глотает ошибку распаковки и просто не матчит фильтр — поэтому в setup()
    последним висит catch-all: без него пользователь получил бы вечный спиннер молча."""
    with pytest.raises((TypeError, ValueError)):
        cls.unpack(legacy)


async def test_setup_registers_catch_all_last():
    from bot.handlers import FLIBUSTA, setup

    flibusta, libgen = FlibustaProvider(transport=_mock()), LibgenProvider(transport=_mock())
    try:
        router = setup({FLIBUSTA: flibusta, LIBGEN: libgen}, object())
        handlers = router.callback_query.handlers
        assert not handlers[-1].filters, "последним должен идти catch-all без фильтров"
        assert all(h.filters for h in handlers[:-1]), "catch-all не должен перехватывать чужое"
    finally:
        await flibusta.aclose()
        await libgen.aclose()


async def test_only_flibusta_can_be_watched():
    """Кнопки «Следить…» вешаются по isinstance — у libgen нет ни OPDS, ни лент по автору."""
    flibusta, libgen = FlibustaProvider(transport=_mock()), LibgenProvider(transport=_mock())
    try:
        assert isinstance(flibusta, WatchSource)
        assert not isinstance(libgen, WatchSource)
    finally:
        await flibusta.aclose()
        await libgen.aclose()
