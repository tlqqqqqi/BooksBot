from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import FLIBUSTA, AuthorCb, BookCb, DownloadCb, PageCb, SrcCb, WatchCb
from bot.formatters import fmt_emoji
from providers.base import Book, SearchHit
from storage import Watch

_PAGE_SIZE = 10


def search_results_kb(
    hits: list[SearchHit],
    page: int,
    has_next: bool,
    kind: str = "search",
    target_id: str = "",
    watch_query: str | None = None,
    src: str = FLIBUSTA,
    other_source: tuple[str, str] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for hit in hits:
        if hit.kind == "book":
            label = f"📚 {hit.title}"
            if hit.author:
                label += f" — {hit.author}"
            builder.button(text=label[:64], callback_data=BookCb(src=src, id=hit.id))
        else:
            builder.button(text=f"✍️ {hit.title}"[:64], callback_data=AuthorCb(id=hit.id))

    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=PageCb(page=page - 1, kind=kind, target_id=target_id, src=src).pack(),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="▶ Далее",
                callback_data=PageCb(page=page + 1, kind=kind, target_id=target_id, src=src).pack(),
            )
        )

    builder.adjust(1)
    if nav:
        builder.row(*nav)
    if other_source is not None:
        code, label = other_source
        builder.row(InlineKeyboardButton(text=label[:64], callback_data=SrcCb(src=code).pack()))
    if watch_query is not None:
        # Запрос кладём прямо в callback, чтобы кнопка была привязана к своему сообщению
        # и переживала рестарт; не влез в 64 байта — фолбэк на последний query из FSM.
        try:
            cb = WatchCb(action="q", target=watch_query).pack()
        except ValueError:
            cb = WatchCb(action="q", target="").pack()
        builder.row(InlineKeyboardButton(text="🔔 Следить за новинками по этому запросу", callback_data=cb))
    return builder.as_markup()


def book_formats_kb(book: Book, src: str = FLIBUSTA) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for dl in book.downloads:
        emoji = fmt_emoji(dl.format)
        builder.button(
            text=f"{emoji} {dl.format.upper()}",
            callback_data=DownloadCb(src=src, book_id=book.id, fmt=dl.format),
        )
    builder.adjust(3)
    return builder.as_markup()


def book_card_kb(book: Book, with_watch: bool = False, src: str = FLIBUSTA) -> InlineKeyboardMarkup:
    """Кнопки карточки книги: форматы + подписка на серию/автора."""
    builder = InlineKeyboardBuilder()
    builder.attach(InlineKeyboardBuilder.from_markup(book_formats_kb(book, src)))
    if with_watch:
        if book.series_id:
            builder.row(
                InlineKeyboardButton(
                    text=f"🔔 Следить за серией «{book.series_name}»"[:64],
                    callback_data=WatchCb(action="s", target=book.series_id).pack(),
                )
            )
        if book.author_id:
            builder.row(
                InlineKeyboardButton(
                    text="🔔 Следить за автором",
                    callback_data=WatchCb(action="a", target=book.author_id).pack(),
                )
            )
    return builder.as_markup()


def watches_kb(watches: list[Watch]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for w in watches:
        builder.button(
            text=f"❌ {w.label}"[:64],
            callback_data=WatchCb(action="del", target=str(w.id)),
        )
    builder.adjust(1)
    return builder.as_markup()
