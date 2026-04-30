from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

HitKind = Literal["book", "author"]


@dataclass(slots=True)
class SearchHit:
    kind: HitKind
    id: str
    title: str
    author: str | None = None


@dataclass(slots=True)
class DownloadLink:
    format: str
    url: str


@dataclass(slots=True)
class Book:
    id: str
    title: str
    author: str | None
    annotation: str | None
    genres: list[str] = field(default_factory=list)
    downloads: list[DownloadLink] = field(default_factory=list)


@dataclass(slots=True)
class DownloadedFile:
    content: bytes
    filename: str
    content_type: str | None = None


class ProviderError(Exception):
    """Базовая ошибка провайдера (сеть, парсинг, 4xx/5xx)."""


class NotFoundError(ProviderError):
    """Запрошенный ресурс отсутствует."""


class BookProvider(ABC):
    name: str

    @abstractmethod
    async def search(
        self, query: str, page: int = 1
    ) -> tuple[list[SearchHit], bool]:
        """Возвращает (результаты, has_next_page)."""

    @abstractmethod
    async def get_book(self, book_id: str) -> Book: ...

    @abstractmethod
    async def get_author_books(
        self, author_id: str, page: int = 1
    ) -> tuple[list[SearchHit], bool, str | None]: ...

    @abstractmethod
    async def download(self, book_id: str, fmt: str) -> DownloadedFile: ...

    @abstractmethod
    def book_url(self, book_id: str) -> str:
        """Прямая ссылка на страницу книги (для fallback'а при больших файлах)."""

    @abstractmethod
    def download_url(self, book_id: str, fmt: str) -> str: ...

    @abstractmethod
    async def aclose(self) -> None: ...
