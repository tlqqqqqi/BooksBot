import html

from providers.base import Book, SearchHit

_MAX_ANNOTATION = 3500
_FORMAT_EMOJI = {"fb2": "📖", "epub": "📕", "mobi": "📗", "pdf": "📄", "djvu": "🗒"}


def escape(text: str) -> str:
    return html.escape(text)


def format_book(book: Book) -> str:
    title = escape(book.title)
    author = escape(book.author or "Автор неизвестен")
    genres = ", ".join(escape(g) for g in book.genres) if book.genres else "—"

    annotation = ""
    if book.annotation:
        ann = book.annotation
        if len(ann) > _MAX_ANNOTATION:
            ann = ann[:_MAX_ANNOTATION].rsplit(" ", 1)[0] + "…"
        annotation = f"\n\n<b>Описание:</b>\n{escape(ann)}"

    return (
        f"<b>{title}</b>\n"
        f"<i>{author}</i>\n"
        f"Жанр: {genres}"
        f"{annotation}"
    )


def format_search_results(hits: list[SearchHit], query: str, page: int) -> str:
    q = escape(query)
    if not hits:
        return f'По запросу <b>"{q}"</b> ничего не найдено.'
    return f'Результаты поиска по <b>"{q}"</b> (стр. {page}):\nВыберите книгу или автора:'


def format_author_books(hits: list[SearchHit], author_name: str, page: int) -> str:
    name = escape(author_name)
    if not hits:
        return f"Книги автора <b>{name}</b> не найдены."
    return f"Книги автора <b>{name}</b> (стр. {page}):"


def fmt_emoji(fmt: str) -> str:
    return _FORMAT_EMOJI.get(fmt, "💾")
