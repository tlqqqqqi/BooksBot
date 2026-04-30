import re
from urllib.parse import quote

import httpx
from loguru import logger
from selectolax.parser import HTMLParser

from config import settings
from providers.base import (
    Book,
    BookProvider,
    DownloadedFile,
    DownloadLink,
    NotFoundError,
    ProviderError,
    SearchHit,
)

# Formats served directly at /b/<id>/<fmt>
_DIRECT_FORMATS = frozenset({"fb2", "epub", "mobi", "txt", "rtf", "lit", "lrf"})
# Formats served via /b/<id>/download endpoint
_DOWNLOAD_ENDPOINT_FORMATS = frozenset({"pdf", "djvu"})


def _text(node) -> str:
    """Extract text from a node, preserving internal whitespace (avoids stripping spaces around <b> tags)."""
    return re.sub(r"\s+", " ", node.text(strip=False)).strip()


class FlibustaProvider(BookProvider):
    name = "flibusta"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.flibusta_base_url,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(settings.request_timeout),
        )

    # ------------------------------------------------------------------ HTTP

    async def _get(self, path: str) -> str:
        try:
            r = await self._client.get(path)
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise NotFoundError(path) from e
            raise ProviderError(f"HTTP {e.response.status_code}: {path}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"Network error: {e}") from e

    # ----------------------------------------------------------------- Search

    async def search(self, query: str, page: int = 1) -> tuple[list[SearchHit], bool]:
        path = f"/booksearch?ask={quote(query)}&page={page - 1}"
        html = await self._get(path)
        hits, has_next = self._parse_search(html)
        logger.debug("search {!r} p{} → {} hits / has_next={}", query, page, len(hits), has_next)
        if not hits:
            # Log first 500 chars to detect unexpected HTML (captcha, 502 page, etc.)
            logger.warning("search {!r} empty — response snippet: {!r}", query, html[:500])
        return hits, has_next

    def _parse_search(self, html: str) -> tuple[list[SearchHit], bool]:
        hits: list[SearchHit] = []
        has_next = False

        # --- Authors ---
        m = re.search(r"Найденные писатели[^<]*</h3>\s*<ul>(.*?)</ul>", html, re.DOTALL)
        if m:
            for li in HTMLParser(f"<ul>{m.group(1)}</ul>").css("li"):
                # Take the first /a/<id> link whose canonical author (not synonym)
                a = li.css_first('a[href^="/a/"]')
                if a:
                    parts = a.attributes.get("href", "").split("/")
                    if len(parts) >= 3 and parts[2].isdigit():
                        hits.append(SearchHit(kind="author", id=parts[2], title=_text(a)))

        # --- Books ---
        m = re.search(
            r"Найденные книги \((\d+) - (\d+) из (\d+)\)[^<]*</h3>\s*<ul>(.*?)</ul>",
            html,
            re.DOTALL,
        )
        if m:
            end, total = int(m.group(2)), int(m.group(3))
            has_next = end < total
            for li in HTMLParser(f"<ul>{m.group(4)}</ul>").css("li"):
                book_a = li.css_first('a[href^="/b/"]')
                if not book_a:
                    continue
                parts = book_a.attributes.get("href", "").split("/")
                if len(parts) < 3 or not parts[2].isdigit():
                    continue
                bid = parts[2]
                title = _text(book_a)
                author_a = li.css_first('a[href^="/a/"]')
                author = _text(author_a) if author_a else None
                hits.append(SearchHit(kind="book", id=bid, title=title, author=author))

        return hits, has_next

    # ------------------------------------------------------------------ Book

    async def get_book(self, book_id: str) -> Book:
        html = await self._get(f"/b/{book_id}")
        return self._parse_book(book_id, html)

    def _parse_book(self, book_id: str, html: str) -> Book:
        tree = HTMLParser(html)

        # Title: <h1 class="title">Name (fb2)</h1> — strip trailing "(format)"
        h1 = tree.css_first("h1.title")
        raw_title = _text(h1) if h1 else ""
        title = re.sub(r"\s*\([^)]+\)\s*$", "", raw_title).strip() or raw_title

        # Author: first /a/<numeric_id> link on the page
        author: str | None = None
        for a in tree.css('a[href^="/a/"]'):
            aid = a.attributes.get("href", "").split("/")[-1]
            if aid.isdigit():
                author = _text(a)
                break

        # Annotation: content between <h2>Аннотация</h2> and <hr>
        annotation: str | None = None
        m = re.search(r"<h2>\s*Аннотация\s*</h2>(.*?)<hr", html, re.DOTALL)
        if m:
            ann_text = HTMLParser(m.group(1)).text(strip=True)
            annotation = ann_text or None

        # Genres
        genres = [a.text(strip=True) for a in tree.css("a.genre") if a.text(strip=True)]

        # Download links: /b/<id>/fb2, /b/<id>/epub, etc.
        downloads = self._parse_downloads(book_id, html)

        return Book(id=book_id, title=title, author=author, annotation=annotation, genres=genres, downloads=downloads)

    def _parse_downloads(self, book_id: str, html: str) -> list[DownloadLink]:
        base = f"/b/{book_id}/"
        tree = HTMLParser(html)
        seen: set[str] = set()
        result: list[DownloadLink] = []

        for a in tree.css(f'a[href^="{base}"]'):
            href: str = a.attributes.get("href", "")
            suffix = href[len(base):]
            link_text = a.text(strip=True).strip("()").lower()

            if suffix in _DIRECT_FORMATS:
                fmt = suffix
            elif suffix == "download":
                # format embedded in text: "скачать pdf" / "скачать djvu"
                m = re.search(r"(pdf|djvu|fb2|epub|mobi)", link_text)
                fmt = m.group(1) if m else "download"
            else:
                continue

            if fmt not in seen:
                seen.add(fmt)
                result.append(DownloadLink(format=fmt, url=settings.flibusta_base_url + href))

        return result

    # --------------------------------------------------------------- Author

    async def get_author_books(self, author_id: str, page: int = 1) -> tuple[list[SearchHit], bool, str | None]:
        html = await self._get(f"/a/{author_id}")
        tree = HTMLParser(html)
        h1 = tree.css_first("h1.title")
        author_name = _text(h1) if h1 else None
        return self._parse_author_books(html), False, author_name

    def _parse_author_books(self, html: str) -> list[SearchHit]:
        tree = HTMLParser(html)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for a in tree.css('a[href^="/b/"]'):
            href = a.attributes.get("href", "")
            parts = href.split("/")
            if len(parts) < 3:
                continue
            bid = parts[2]
            if not bid.isdigit() or bid in seen:
                continue
            title = _text(a)
            if not title:
                continue
            seen.add(bid)
            hits.append(SearchHit(kind="book", id=bid, title=title))
        return hits

    # -------------------------------------------------------------- Download

    async def download(self, book_id: str, fmt: str) -> DownloadedFile:
        path = f"/b/{book_id}/download" if fmt in _DOWNLOAD_ENDPOINT_FORMATS else f"/b/{book_id}/{fmt}"
        try:
            r = await self._client.get(path)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"Download failed: HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise ProviderError(f"Download network error: {e}") from e

        content = r.content
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename[^;=\n]*=\s*["\']?([^"\';\n]+)', cd)
        filename = m.group(1).strip() if m else f"book_{book_id}.{fmt}"

        return DownloadedFile(content=content, filename=filename, content_type=r.headers.get("content-type"))

    # ---------------------------------------------------------------- URLs

    def book_url(self, book_id: str) -> str:
        return f"{settings.flibusta_base_url}/b/{book_id}"

    def download_url(self, book_id: str, fmt: str) -> str:
        path = f"/b/{book_id}/download" if fmt in _DOWNLOAD_ENDPOINT_FORMATS else f"/b/{book_id}/{fmt}"
        return f"{settings.flibusta_base_url}{path}"

    async def aclose(self) -> None:
        await self._client.aclose()
