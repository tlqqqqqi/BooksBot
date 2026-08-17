"""CallbackData-фабрики.

Поле `src` — код источника (см. handlers.FLIBUSTA/LIBGEN). Без него id книги
двусмыслен: у Флибусты это числовой id страницы, у libgen — md5 файла.
Пакуется коротким кодом ("f"/"l"), чтобы md5 (32 символа) влезал в лимит 64 байта.
"""

from aiogram.filters.callback_data import CallbackData

# Коды источников живут здесь, а не в handlers: их импортируют и keyboards, и watcher,
# а импорт handlers оттуда замкнул бы цикл.
FLIBUSTA = "f"
LIBGEN = "l"


class BookCb(CallbackData, prefix="b"):
    src: str
    id: str


class AuthorCb(CallbackData, prefix="a"):
    id: str  # страницы авторов есть только у Флибусты


class DownloadCb(CallbackData, prefix="dl"):
    src: str
    book_id: str
    fmt: str


class PageCb(CallbackData, prefix="pg"):
    page: int
    kind: str  # "search" | "author"
    target_id: str  # author_id for kind="author", empty for kind="search"
    src: str


class SrcCb(CallbackData, prefix="sr"):
    """Повторить текущий запрос в другом источнике. Запрос берётся из FSM."""

    src: str


class WatchCb(CallbackData, prefix="w"):
    action: str  # "s" (серия) | "a" (автор) | "q" (запрос) | "del"
    target: str  # series_id / author_id / watch_id; для "q" пусто — запрос берётся из FSM
