from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import AuthorCb, BookCb, DownloadCb, PageCb
from bot.formatters import fmt_emoji
from providers.base import Book, SearchHit

_PAGE_SIZE = 10


def search_results_kb(
    hits: list[SearchHit],
    page: int,
    has_next: bool,
    kind: str = "search",
    target_id: str = "",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for hit in hits:
        if hit.kind == "book":
            label = f"📚 {hit.title}"
            if hit.author:
                label += f" — {hit.author}"
            builder.button(text=label[:64], callback_data=BookCb(id=hit.id))
        else:
            builder.button(text=f"✍️ {hit.title}"[:64], callback_data=AuthorCb(id=hit.id))

    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=PageCb(page=page - 1, kind=kind, target_id=target_id).pack(),
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="▶ Далее",
                callback_data=PageCb(page=page + 1, kind=kind, target_id=target_id).pack(),
            )
        )

    builder.adjust(1)
    if nav:
        builder.row(*nav)
    return builder.as_markup()


def book_formats_kb(book: Book) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for dl in book.downloads:
        emoji = fmt_emoji(dl.format)
        builder.button(
            text=f"{emoji} {dl.format.upper()}",
            callback_data=DownloadCb(book_id=book.id, fmt=dl.format),
        )
    builder.adjust(3)
    return builder.as_markup()
