"""Library Genesis — источник иностранных книг.

Отличия от Флибусты, из которых следует всё остальное в этом файле:

* **id книги — md5 файла.** Он лежит прямо в строке выдачи (колонка зеркал), одинаков
  на всех зеркалах и переживает их смену, поэтому в callback уходит именно он, а не
  внутренний числовой id, который у зеркал свой.
* **Запись — на файл, а не на произведение.** Одно издание приезжает пятью почти
  одинаковыми строками, поэтому выдача схлопывается по паре «название + автор».
* **Ключ скачивания одноразовый.** `ads.php` каждый раз выдаёт новый `get.php?...&key=`,
  так что прямую ссылку нельзя ни закэшировать, ни отдать пользователю — наружу уходит
  адрес страницы `ads.php`, где браузер возьмёт свежий ключ сам.
* **Подписок нет.** У libgen нет ни OPDS, ни лент по автору/серии (`rss.php` — глобальная
  лента новинок), поэтому `WatchSource` сознательно не реализован: кнопки «Следить…»
  для этого источника просто не появятся.
"""

import asyncio
import html
import re

import httpx
from loguru import logger
from selectolax.parser import HTMLParser

from config import settings
from providers.base import (
    Book,
    BookProvider,
    DownloadedFile,
    DownloadLink,
    FileTooLargeError,
    NotFoundError,
    ProviderError,
    SearchHit,
)

# Зеркала libgen регулярно отдают 5xx и рвут соединения — одиночный запрос слишком хрупок.
_RETRY_DELAYS = (1.0, 3.0)

# Сколько записей просим у index.php за раз; столько же он отдаёт на страницу.
_PAGE_SIZE = 25

# Темы каталога: l — non-fiction, f — художественная. Без фильтра выдачу забивают
# журнальные статьи scimag («too big to fail» находит десяток работ по банковскому надзору).
_BOOK_TOPICS = ("l", "f")

# Чем ниже индекс, тем охотнее формат выбирается из группы дублей.
_EXT_PREFERENCE = ("epub", "fb2", "mobi", "azw3", "djvu", "pdf")

_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_MD5_IN_HREF = re.compile(r"md5=([0-9a-f]{32})")

# В именах файлов libgen сохраняет HTML-сущности, заменив в них '#' и ';' на '_':
# "&_039_s" вместо "&#039;s", "&amp_" вместо "&amp;". Возвращаем разделители и раскрываем.
_MANGLED_ENTITY = re.compile(r"&(?:_(\d+)_|([a-zA-Z]{2,8})_)")


def _clean_filename(name: str) -> str:
    restored = _MANGLED_ENTITY.sub(lambda m: f"&#{m.group(1)};" if m.group(1) else f"&{m.group(2)};", name)
    return html.unescape(restored)


def _norm(text: str) -> str:
    """Ключ схлопывания: регистр и пунктуация не должны разводить дубли.

    `[\\W_]` юникодный, поэтому иероглифы, вязь и диакритика остаются в ключе. Список
    вида [a-zа-я] обнулял бы ключ у любого нелатинского названия, и все китайские книги
    в выдаче схлопнулись бы в одну — для источника иностранных книг это фатально."""
    return re.sub(r"[\W_]+", " ", text.lower(), flags=re.UNICODE).strip()


def _norm_author(text: str) -> str:
    """Ключ автора: без учёта порядка слов, ролей и соавторов.

    «Ferguson, Niall», «Niall Ferguson», «Ferguson, Niall(Author)» и
    «Ferguson, Niall; Prebble, Simon» — всё это одна и та же книга в выдаче libgen.
    Для названий так делать нельзя: там перестановка слов меняет книгу."""
    first = text.split(";")[0]  # соавторы/чтецы указаны не везде
    first = re.sub(r"\([^)]*\)", " ", first)  # «(Author)», «(editor)»
    return " ".join(sorted(_norm(first).split()))


def _anchor_title(anchor) -> str:
    """Только собственный текст ссылки.

    Маркер издания лежит вложенным <i> и при обычном .text() приклеивается к названию
    без пробела: «…of the WorldE-Audiobook» — из-за этого дубли не схлопывались."""
    parts: list[str] = []
    for node in anchor.iter(include_text=True):
        if node.tag != "-text":
            break
        parts.append(node.text(strip=False))
    text = "".join(parts) or anchor.text(strip=False)
    return re.sub(r"\s+", " ", text).strip()


class LibgenProvider(BookProvider):
    name = "libgen"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url=settings.libgen_base_url,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
        )

    # ------------------------------------------------------------------ HTTP

    async def _request(self, path: str, params: dict | None = None) -> httpx.Response:
        last_attempt = len(_RETRY_DELAYS)
        for attempt in range(last_attempt + 1):
            try:
                r = await self._client.get(path, params=params)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 404:
                    raise NotFoundError(path) from e
                err = ProviderError(f"HTTP {code}: {path}")
                cause: Exception = e
                retryable = code >= 500
            except httpx.RequestError as e:
                err = ProviderError(f"Network error: {type(e).__name__}({e}): {path}")
                cause = e
                retryable = True
            if not retryable or attempt == last_attempt:
                raise err from cause
            delay = _RETRY_DELAYS[attempt]
            logger.info("{} — retry {}/{} in {}s", err, attempt + 1, last_attempt, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _fetch_file(self, url: httpx.URL) -> httpx.Response:
        """Скачивание файла с проверкой размера ДО чтения тела.

        Обычный GET сначала буферизует весь ответ и только потом позволяет посмотреть длину,
        а у libgen попадаются многогигабайтные сканы — такой файл выел бы память процесса
        ради того, чтобы затем ответить «больше 50 МБ, вот ссылка»."""
        last_attempt = len(_RETRY_DELAYS)
        for attempt in range(last_attempt + 1):
            try:
                response = await self._client.send(self._client.build_request("GET", url), stream=True)
                try:
                    response.raise_for_status()
                    declared = int(response.headers.get("content-length") or 0)
                    if declared > settings.max_file_size_bytes:
                        raise FileTooLargeError(f"libgen: {declared} bytes: {url}")
                    await response.aread()
                    return response
                finally:
                    await response.aclose()
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 404:
                    raise NotFoundError(str(url)) from e
                err = ProviderError(f"HTTP {code}: {url}")
                cause: Exception = e
                retryable = code >= 500
            except httpx.RequestError as e:
                err = ProviderError(f"Network error: {type(e).__name__}({e}): {url}")
                cause = e
                retryable = True
            if not retryable or attempt == last_attempt:
                raise err from cause
            await asyncio.sleep(_RETRY_DELAYS[attempt])
        raise AssertionError("unreachable")

    # ----------------------------------------------------------------- Search

    async def search(self, query: str, page: int = 1) -> tuple[list[SearchHit], bool]:
        r = await self._request(
            "/index.php",
            params={"req": query, "res": _PAGE_SIZE, "page": page, "topics[]": list(_BOOK_TOPICS)},
        )
        rows, raw_count = self._parse_rows(r.text)
        hits = self._collapse(rows)
        # Пагинация серверная; страница заполнена под завязку — значит есть следующая.
        # Считаем именно строки выдачи: одна отфильтрованная строка не должна убирать «Далее».
        has_next = raw_count >= _PAGE_SIZE
        logger.debug("libgen search {!r} p{} → {} rows / {} hits", query, page, raw_count, len(hits))
        if not rows:
            logger.warning("libgen search {!r} empty — snippet: {!r}", query, r.text[:500])
        return hits, has_next

    @staticmethod
    def _parse_rows(html: str) -> tuple[list[dict], int]:
        """Строки выдачи и их исходное число (до фильтрации — по нему считается has_next).

        Колонки: 0 название, 1 автор, 6 размер, 7 расширение, 8 зеркала."""
        tree = HTMLParser(html)
        table = tree.css_first("table#tablelibgen") or tree.css_first("table")
        if table is None:
            return [], 0

        rows: list[dict] = []
        raw_count = 0
        for tr in table.css("tbody tr"):
            tds = tr.css("td")
            if len(tds) < 9:
                continue
            raw_count += 1

            mirror = tds[8].css_first('a[href*="md5="]')
            m = _MD5_IN_HREF.search(mirror.attributes.get("href", "")) if mirror else None
            if not m:
                continue  # нет зеркала — скачать нечего

            ext = tds[7].text(strip=True).lower()
            if not ext:
                continue

            title_a = tds[0].css_first('a[href*="edition.php"]')
            title = _anchor_title(title_a) if title_a else ""
            if not title:
                continue

            rows.append(
                {
                    "md5": m.group(1),
                    "title": title,
                    "author": tds[1].text(strip=True) or None,
                    "ext": ext,
                    "size": LibgenProvider._size_bytes(tds[6].text(strip=True)),
                }
            )
        return rows, raw_count

    @staticmethod
    def _size_bytes(text: str) -> int:
        """Размер нужен только для выбора лучшего дубля, поэтому мусор — это 0, а не исключение.

        Жадное [\\d.]+ ловило бы «1.2.3», float на нём падает, а ValueError — не ProviderError
        и снёс бы хендлер поиска целиком."""
        m = re.match(r"(\d+(?:\.\d+)?)\s*([kmg]?b)", text.strip(), re.I)
        if not m:
            return 0
        mult = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}[m.group(2).lower()]
        return int(float(m.group(1)) * mult)

    @staticmethod
    def _collapse(rows: list[dict]) -> list[SearchHit]:
        """Одно произведение — одна строка: из группы дублей берём удобнейший формат."""
        best: dict[tuple[str, str], dict] = {}
        order: list[tuple[str, str]] = []

        for row in rows:
            key = (_norm(row["title"]), _norm_author(row["author"] or ""))
            current = best.get(key)
            if current is None:
                best[key] = row
                order.append(key)
            elif LibgenProvider._rank(row) < LibgenProvider._rank(current):
                best[key] = row

        return [
            SearchHit(
                kind="book",
                id=best[key]["md5"],
                title=best[key]["title"],
                author=best[key]["author"],
            )
            for key in order
        ]

    @staticmethod
    def _rank(row: dict) -> tuple[int, int]:
        """Меньше — лучше: сначала предпочтительный формат, при равенстве — файл покрупнее."""
        try:
            ext_rank = _EXT_PREFERENCE.index(row["ext"])
        except ValueError:
            ext_rank = len(_EXT_PREFERENCE)
        return (ext_rank, -row["size"])

    # ------------------------------------------------------------------ Book

    async def get_book(self, book_id: str) -> Book:
        md5 = self._check_md5(book_id)
        html = (await self._request("/ads.php", params={"md5": md5})).text
        fields = self._parse_bibtex(html)

        title = fields.get("title") or f"Книга {md5[:8]}"
        ext = await self._extension(md5)

        # У libgen нет аннотаций — вместо них показываем выходные данные, они там есть всегда.
        imprint = ", ".join(v for v in (fields.get("publisher"), fields.get("year")) if v)

        return Book(
            id=md5,
            title=title,
            author=fields.get("author"),
            annotation=imprint or None,
            downloads=[DownloadLink(format=ext, url=self.download_url(md5, ext))],
        )

    async def _extension(self, md5: str) -> str:
        """Расширение живёт только в json.php; его падение не должно ронять карточку."""
        try:
            r = await self._request("/json.php", params={"object": "f", "addkeys": "*", "md5": md5})
            record = next(iter(r.json().values()))
            return (record.get("extension") or "").lower() or "download"
        except (ProviderError, StopIteration, ValueError, AttributeError) as e:
            logger.info("libgen json.php {}: {} — формат неизвестен", md5, e)
            return "download"

    @staticmethod
    def _parse_bibtex(html: str) -> dict[str, str]:
        """Метаданные берём из BibTeX-блока: он машинный и переживает правки вёрстки."""
        area = HTMLParser(html).css_first("textarea")
        text = area.text() if area else html

        fields: dict[str, str] = {}
        for name in ("title", "author", "publisher", "year"):
            m = re.search(rf"^\s*{name}\s*=\s*\{{(.*)\}},?\s*$", text, re.MULTILINE)
            if m and m.group(1).strip():
                fields[name] = m.group(1).strip()
        return fields

    @staticmethod
    def _check_md5(book_id: str) -> str:
        md5 = book_id.strip().lower()
        if not _MD5_RE.match(md5):
            raise NotFoundError(f"libgen: не md5: {book_id!r}")
        return md5

    # --------------------------------------------------------------- Author

    async def get_author_books(self, author_id: str, page: int = 1) -> tuple[list[SearchHit], bool, str | None]:
        """У libgen нет страниц авторов с id — ищем по имени. Хиты kind="author" провайдер
        не порождает, так что сюда попадают только явные переходы."""
        hits, has_next = await self.search(author_id, page=page)
        return hits, has_next, author_id

    # -------------------------------------------------------------- Download

    async def download(self, book_id: str, fmt: str) -> DownloadedFile:
        md5 = self._check_md5(book_id)
        ads = (await self._request("/ads.php", params={"md5": md5})).text

        link = next(
            (
                a.attributes.get("href", "")
                for a in HTMLParser(ads).css("a")
                if "get.php" in (a.attributes.get("href") or "")
            ),
            None,
        )
        if not link:
            raise ProviderError(f"libgen: download link not found for {md5}")

        # Ссылка бывает относительной ("get.php?…") и абсолютной на CDN другого зеркала —
        # join разбирает оба случая, а склейка через "/" из абсолютной делала бы мусор.
        r = await self._fetch_file(self._client.base_url.join(link))

        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename[^;=\n]*=\s*["\']?([^"\';\n]+)', cd)
        filename = _clean_filename(m.group(1).strip()) if m else f"book_{md5[:8]}.{fmt}"

        return DownloadedFile(content=r.content, filename=filename, content_type=r.headers.get("content-type"))

    # ---------------------------------------------------------------- URLs

    def book_url(self, book_id: str) -> str:
        return f"{settings.libgen_base_url}/file.php?md5={book_id}"

    def download_url(self, book_id: str, fmt: str) -> str:
        # Не get.php: его ключ одноразовый и протухнет раньше, чем пользователь нажмёт.
        return f"{settings.libgen_base_url}/ads.php?md5={book_id}"

    async def aclose(self) -> None:
        await self._client.aclose()
