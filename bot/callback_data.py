from aiogram.filters.callback_data import CallbackData


class BookCb(CallbackData, prefix="b"):
    id: str


class AuthorCb(CallbackData, prefix="a"):
    id: str


class DownloadCb(CallbackData, prefix="dl"):
    book_id: str
    fmt: str


class PageCb(CallbackData, prefix="pg"):
    page: int
    kind: str  # "search" | "author"
    target_id: str  # author_id for kind="author", empty for kind="search"
