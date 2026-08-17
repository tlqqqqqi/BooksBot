"""LibgenProvider: парсинг выдачи, схлопывание дублей, скачивание (MockTransport, без сети).

Разметка в фикстурах повторяет реальную: таблица #tablelibgen, md5 в колонке зеркал,
метаданные карточки — из BibTeX-блока ads.php, расширение — из json.php.
"""

import httpx
import pytest

import providers.libgen as lg
from config import settings
from providers.base import FileTooLargeError, NotFoundError, ProviderError

MD5_A = "a" * 32
MD5_B = "b" * 32


def _row(
    title: str, author: str, ext: str, size: str, md5: str, lang: str = "English", edition: str = ""
) -> str:
    """Разметка как на живой странице: маркер издания — вложенный <i> внутри ссылки-заголовка."""
    return (
        "<tr>"
        f'<td><a href="edition.php?id=1">{title} <i>{edition}</i></a>'
        f'<a href="edition.php?id=1">9781410415332</a></td>'
        f"<td>{author}</td><td>Penguin</td><td>2008</td><td>{lang}</td><td>442</td>"
        f'<td><a href="/file.php?id=777">{size}</a></td>'
        f"<td>{ext}</td>"
        f'<td><a href="/ads.php?md5={md5}">1</a><a href="https://libgen.pw/book/{md5}">2</a></td>'
        "</tr>"
    )


def search_html(*rows: str) -> str:
    return (
        '<table id="tablelibgen" class="table table-striped"><thead><tr>'
        "<th>Title</th><th>Author(s)</th><th>Publisher</th><th>Year</th><th>Language</th>"
        "<th>Pages</th><th>Size</th><th>Ext.</th><th>Mirrors</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


ADS_HTML = (
    "<html><body><h2>GET</h2>"
    f'<a href="get.php?md5={MD5_A}&key=FRESHKEY123">GET</a>'
    "<textarea>@book{book:{93401177},\n"
    "   title =     {The Ascent of Money: A Financial History of the World},\n"
    "   author =    {Ferguson, Niall; Prebble, Simon},\n"
    "   publisher = {Penguin Press},\n"
    "   year =      {2008},\n"
    f"   url =       {{libgen.li/file.php?md5={MD5_A}}}}}</textarea>"
    "</body></html>"
)

JSON_FILE = f'{{"93401177": {{"md5": "{MD5_A}", "extension": "epub", "filesize": "2437338", "pages": "442"}}}}'


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(lg, "_RETRY_DELAYS", (0, 0))


def make_provider(handler) -> lg.LibgenProvider:
    return lg.LibgenProvider(transport=httpx.MockTransport(handler))


def routed(*, search: str = "", ads: str = ADS_HTML, js: str = JSON_FILE, blob: bytes = b"PK\x03\x04data"):
    """Один handler на все эндпоинты libgen."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/index.php":
            return httpx.Response(200, text=search)
        if path == "/ads.php":
            return httpx.Response(200, text=ads)
        if path == "/json.php":
            return httpx.Response(200, text=js)
        if path == "/get.php":
            return httpx.Response(
                200, content=blob, headers={"content-disposition": 'attachment; filename="ascent.epub"'}
            )
        return httpx.Response(404)

    return handler


# ------------------------------------------------------------------ search


async def test_search_parses_title_author_and_md5_as_id():
    html = search_html(_row("The Ascent of Money", "Niall Ferguson", "epub", "3 MB", MD5_A))
    p = make_provider(routed(search=html))
    hits, has_next = await p.search("ascent of money")

    assert len(hits) == 1
    assert hits[0].id == MD5_A  # id книги — md5, он же живёт в callback'е
    assert hits[0].title == "The Ascent of Money"
    assert hits[0].author == "Niall Ferguson"
    assert hits[0].kind == "book"
    assert has_next is False


async def test_search_collapses_duplicate_editions_preferring_epub():
    """Libgen хранит запись на файл: одно издание приезжает пятью строками."""
    html = search_html(
        _row("The Ascent of Money", "Niall Ferguson", "pdf", "3 MB", MD5_A),
        _row("the ascent of money", "Ferguson, Niall", "epub", "2 MB", MD5_B),
        _row("The Ascent of Money", "Niall Ferguson", "azw3", "4 MB", "c" * 32),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("ascent of money")

    assert len(hits) == 1
    assert hits[0].id == MD5_B  # epub выигрывает у pdf и azw3


async def test_search_ignores_edition_marker_glued_into_title():
    """<i>E-Audiobook</i> внутри ссылки прилипает к названию без пробела и разводит дубли."""
    html = search_html(
        _row("The Ascent of Money", "Ferguson, Niall", "pdf", "3 MB", MD5_A),
        _row("The Ascent of Money", "Ferguson, Niall", "epub", "2 MB", MD5_B, edition="E-Audiobook"),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("x")

    assert len(hits) == 1
    assert hits[0].title == "The Ascent of Money"  # без хвоста "E-Audiobook"


async def test_search_collapses_role_markers_and_coauthors():
    """«Ferguson, Niall(Author)» и «Ferguson, Niall; Prebble, Simon» — тот же автор."""
    html = search_html(
        _row("The Ascent of Money", "Niall Ferguson", "pdf", "3 MB", MD5_A),
        _row("The Ascent of Money", "Ferguson, Niall(Author)", "azw3", "3 MB", MD5_B),
        _row("The Ascent of Money", "Ferguson, Niall; Prebble, Simon", "epub", "2 MB", "d" * 32),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("x")

    assert len(hits) == 1
    assert hits[0].id == "d" * 32  # epub


async def test_search_keeps_different_books_apart():
    """Схлопывание не должно склеивать разные книги одного автора."""
    html = search_html(
        _row("Sapiens", "Harari", "epub", "3 MB", MD5_A),
        _row("Homo Deus", "Harari", "epub", "3 MB", MD5_B),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("harari")

    assert {h.title for h in hits} == {"Sapiens", "Homo Deus"}


async def test_search_keeps_non_latin_titles_apart():
    """Иероглифы не входят в [a-zа-я]: при наивной нормализации ключ пустеет и разные книги слипаются."""
    html = search_html(
        _row("红楼梦", "曹雪芹", "epub", "3 MB", MD5_A),
        _row("三国演义", "罗贯中", "epub", "3 MB", MD5_B),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("中文")

    assert {h.title for h in hits} == {"红楼梦", "三国演义"}


def test_size_never_raises_on_malformed_input():
    """_size_bytes зовётся из search; ValueError оттуда не ProviderError и снёс бы хендлер."""
    for junk in ("1.2.3 MB", ".. MB", "", "неизвестно", "MB", "1,5 MB"):
        assert isinstance(lg.LibgenProvider._size_bytes(junk), int)


async def test_search_keeps_pagination_when_a_row_is_filtered_out():
    """has_next должен считать строки выдачи, а не выжившие после фильтра."""
    rows = [_row(f"Book {i}", "A", "epub", "1 MB", f"{i:032x}") for i in range(lg._PAGE_SIZE - 1)]
    rows.append(  # мусорная строка: ни md5, ни расширения
        '<tr><td><a href="edition.php?id=9">Broken</a></td><td>X</td><td>P</td><td>2008</td>'
        "<td>English</td><td>1</td><td>1 MB</td><td></td><td></td></tr>"
    )
    p = make_provider(routed(search=search_html(*rows)))
    _, has_next = await p.search("many")

    assert has_next is True


async def test_download_handles_absolute_get_php_url():
    """Часть зеркал отдаёт скачивание с другого хоста — склейка через '/' дала бы мусор."""
    ads = f'<a href="https://cdn.libgen.example/get.php?md5={MD5_A}&key=K9">GET</a>'
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ads)
        return httpx.Response(200, content=b"bytes")

    p = make_provider(handler)
    file = await p.download(MD5_A, "epub")

    assert file.content == b"bytes"
    assert "https://cdn.libgen.example/get.php" in seen[-1]


async def test_download_refuses_file_over_limit_without_reading_it():
    """Libgen хостит многогигабайтные сканы: читать их в память ради проверки размера нельзя."""
    body_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal body_reads
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ADS_HTML)
        body_reads += 1
        huge = settings.max_file_size_bytes * 4
        return httpx.Response(200, content=b"x", headers={"content-length": str(huge)})

    p = make_provider(handler)
    with pytest.raises(FileTooLargeError):
        await p.download(MD5_A, "epub")


async def test_search_skips_rows_without_md5_or_extension():
    html = search_html(
        _row("Good Book", "Author", "epub", "1 MB", MD5_A),
        '<tr><td><a href="edition.php?id=2">No Mirror</a></td><td>X</td><td>P</td><td>2008</td>'
        "<td>English</td><td>1</td><td>1 MB</td><td>epub</td><td></td></tr>",
        _row("No Extension", "Author", "", "1 MB", MD5_B),
    )
    p = make_provider(routed(search=html))
    hits, _ = await p.search("x")

    assert [h.title for h in hits] == ["Good Book"]


async def test_search_reports_next_page_when_page_is_full():
    html = search_html(*[_row(f"Book {i}", "A", "epub", "1 MB", f"{i:032x}") for i in range(lg._PAGE_SIZE)])
    p = make_provider(routed(search=html))
    _, has_next = await p.search("many")
    assert has_next is True


async def test_search_requests_book_topics_and_page():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, text=search_html())

    p = make_provider(handler)
    await p.search("harari", page=3)

    q = seen[0].params
    assert q["req"] == "harari"
    assert q["page"] == "3"
    # Без фильтра тем выдачу забивают журнальные статьи scimag. Проверяем именно значения
    # параметра, а не подстроку в query — подстрока прошла бы и на "topics[]=blah".
    assert q.get_list("topics[]") == ["l", "f"]


# -------------------------------------------------------------------- book


async def test_get_book_reads_bibtex_and_extension():
    p = make_provider(routed())
    book = await p.get_book(MD5_A)

    assert book.title == "The Ascent of Money: A Financial History of the World"
    assert book.author == "Ferguson, Niall; Prebble, Simon"
    assert [d.format for d in book.downloads] == ["epub"]


async def test_get_book_survives_json_failure():
    """json.php даёт только расширение — его падение не должно ронять карточку."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/json.php":
            return httpx.Response(503)
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ADS_HTML)
        return httpx.Response(404)

    p = make_provider(handler)
    book = await p.get_book(MD5_A)

    assert book.title.startswith("The Ascent of Money")
    assert [d.format for d in book.downloads] == ["download"]


async def test_get_book_rejects_bad_md5_without_request():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=ADS_HTML)

    p = make_provider(handler)
    with pytest.raises(NotFoundError):
        await p.get_book("not-an-md5")
    assert calls == 0


# ---------------------------------------------------------------- download


async def test_download_fetches_fresh_key_from_ads_page():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ADS_HTML)
        if request.url.path == "/get.php":
            return httpx.Response(
                200, content=b"PK\x03\x04", headers={"content-disposition": 'attachment; filename="ascent.epub"'}
            )
        return httpx.Response(404)

    p = make_provider(handler)
    file = await p.download(MD5_A, "epub")

    assert file.content == b"PK\x03\x04"
    assert file.filename == "ascent.epub"
    assert any("ads.php" in u for u in seen)
    assert any("key=FRESHKEY123" in u for u in seen)


async def test_download_repairs_mangled_entities_in_filename():
    """Libgen отдаёт имена с HTML-сущностями, где '#' и ';' заменены на '_'.

    Живой пример: "Liar&_039_s Poker … Norton &amp_ Company.epub" — так оно и уедет
    в Telegram названием документа, если не починить."""
    mangled = "[Liar&_039_s Poker] Michael Lewis (1989, W. W. Norton &amp_ Company).epub"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ADS_HTML)
        return httpx.Response(
            200, content=b"x", headers={"content-disposition": f'attachment; filename="{mangled}"'}
        )

    p = make_provider(handler)
    file = await p.download(MD5_A, "epub")

    assert file.filename == "[Liar's Poker] Michael Lewis (1989, W. W. Norton & Company).epub"


async def test_download_fails_clearly_when_key_missing():
    p = make_provider(routed(ads="<html><body>no link here</body></html>"))
    with pytest.raises(ProviderError, match="download link"):
        await p.download(MD5_A, "epub")


async def test_download_url_points_at_ads_page_not_ephemeral_key():
    """Ключ get.php одноразовый — в Telegram уходит страница, где браузер возьмёт свежий."""
    p = make_provider(routed())
    url = p.download_url(MD5_A, "epub")

    assert url.endswith(f"/ads.php?md5={MD5_A}")
    assert "key=" not in url


async def test_download_retries_on_5xx():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/ads.php":
            return httpx.Response(200, text=ADS_HTML)
        attempts += 1
        if attempts < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"ok")

    p = make_provider(handler)
    file = await p.download(MD5_A, "epub")
    assert file.content == b"ok"
    assert attempts == 3
