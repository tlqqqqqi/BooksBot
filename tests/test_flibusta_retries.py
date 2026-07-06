"""Ретраи и сообщения об ошибках FlibustaProvider (httpx.MockTransport, без сети)."""

import httpx
import pytest

import providers.flibusta as fl
from providers.base import NotFoundError, ProviderError


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(fl, "_RETRY_DELAYS", (0, 0))


def make_provider(handler) -> fl.FlibustaProvider:
    return fl.FlibustaProvider(transport=httpx.MockTransport(handler))


async def test_download_retries_on_5xx_then_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"book-bytes")

    p = make_provider(handler)
    file = await p.download("1", "fb2")
    assert file.content == b"book-bytes"
    assert attempts == 3


async def test_download_gives_up_after_all_attempts():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    p = make_provider(handler)
    with pytest.raises(ProviderError):
        await p.download("1", "fb2")
    assert attempts == 3


async def test_get_retries_on_network_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text="<html></html>")

    p = make_provider(handler)
    hits, has_next = await p.search("test")
    assert (hits, has_next) == ([], False)
    assert attempts == 3


async def test_error_message_includes_exception_type():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")  # httpx-таймауты стрингифицируются в пустую строку

    p = make_provider(handler)
    with pytest.raises(ProviderError, match="ReadTimeout"):
        await p.download("1", "fb2")


async def test_no_retry_on_404():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    p = make_provider(handler)
    with pytest.raises(NotFoundError):
        await p.get_book("1")
    assert attempts == 1
