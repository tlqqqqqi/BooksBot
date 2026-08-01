import random
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from loguru import logger

from bot import auth
from bot.callback_data import AuthorCb, BookCb, DownloadCb, PageCb, WatchCb
from bot.formatters import escape, format_author_books, format_book, format_search_results, format_watches
from bot.keyboards import book_card_kb, search_results_kb, watches_kb
from config import settings
from providers.base import BookProvider, NotFoundError, ProviderError, WatchKind, WatchSource
from storage import Storage

router = Router()

_WELCOME = (
    "👋 <b>КНИЖНЫЙ БОГ снизошел до тебя</b>\n\n"
    "Отправь название книги или имя автора — я найду всё, что есть на Флибусте.\n\n"
    "Можно скачать книгу в форматах <b>fb2, epub, mobi, pdf</b> и других.\n\n"
    "🔔 Кнопки «Следить…» на карточке книги и под поиском подпишут тебя на новинки; "
    "список подписок — /watches."
)


class PasswordState(StatesGroup):
    waiting = State()


class SearchState(StatesGroup):
    query = State()
    author_id = State()
    author_name = State()


def setup(provider: BookProvider, storage: Storage) -> Router:
    watchable = isinstance(provider, WatchSource)

    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if auth.is_authorized(message.from_user.id):
            await message.answer(_WELCOME, parse_mode="HTML")
        else:
            await state.set_state(PasswordState.waiting)
            await message.answer("Введите пароль:")

    @router.message(PasswordState.waiting)
    async def handle_password(message: Message, state: FSMContext) -> None:
        if (message.text or "").strip() == settings.bot_password:
            auth.authorize(message.from_user.id)
            await state.clear()
            await message.answer(_WELCOME, parse_mode="HTML")
        else:
            await message.answer("Неверный пароль. Попробуйте ещё раз.")

    @router.message(Command("watches"))
    async def cmd_watches(message: Message) -> None:
        if not auth.is_authorized(message.from_user.id):
            await message.answer("Введите /start для начала работы.")
            return
        watches = await storage.list_watches(message.from_user.id)
        await message.answer(format_watches(watches), parse_mode="HTML", reply_markup=watches_kb(watches))

    # ~startswith("/"): команды не должны улетать в поиск (catch-all регистрируется последним)
    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_search(message: Message, state: FSMContext) -> None:
        if not auth.is_authorized(message.from_user.id):
            await message.answer("Введите /start для начала работы.")
            return
        query = (message.text or "").strip()
        if not query:
            return
        await state.update_data(query=query, author_id="", author_name="")
        await _do_search(message, state, provider, query=query, page=1, edit=False, watch_query=watchable)

    @router.callback_query(PageCb.filter())
    async def handle_page(callback: CallbackQuery, callback_data: PageCb, state: FSMContext) -> None:
        if not auth.is_authorized(callback.from_user.id):
            await callback.answer("Сначала введите /start.", show_alert=True)
            return
        await callback.answer()
        page = callback_data.page
        if callback_data.kind == "author":
            data = await state.get_data()
            author_id = callback_data.target_id or data.get("author_id", "")
            author_name = data.get("author_name", "")
            await _do_author(callback.message, state, provider, author_id, author_name, page, edit=True)
        else:
            data = await state.get_data()
            query = data.get("query", "")
            if not query:
                await callback.message.answer("Начните поиск заново — отправьте запрос текстом.")
                return
            await _do_search(
                callback.message, state, provider, query=query, page=page, edit=True, watch_query=watchable
            )

    @router.callback_query(BookCb.filter())
    async def handle_book(callback: CallbackQuery, callback_data: BookCb, state: FSMContext) -> None:
        if not auth.is_authorized(callback.from_user.id):
            await callback.answer("Сначала введите /start.", show_alert=True)
            return
        await callback.answer()
        book_id = callback_data.id
        try:
            book = await provider.get_book(book_id)
        except NotFoundError:
            await callback.message.answer("Книга не найдена.")
            return
        except ProviderError as e:
            logger.warning("get_book {}: {}", book_id, e)
            await callback.message.answer("Ошибка при загрузке книги. Попробуйте позже.")
            return

        text = format_book(book)
        kb = book_card_kb(book, with_watch=watchable)
        if book.downloads:
            text += "\n\n<b>Скачать:</b>"
        else:
            text += "\n\n⚠️ Форматы для скачивания не найдены."
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @router.callback_query(AuthorCb.filter())
    async def handle_author(callback: CallbackQuery, callback_data: AuthorCb, state: FSMContext) -> None:
        if not auth.is_authorized(callback.from_user.id):
            await callback.answer("Сначала введите /start.", show_alert=True)
            return
        await callback.answer()
        author_id = callback_data.id
        try:
            hits, has_next, parsed_name = await provider.get_author_books(author_id)
        except ProviderError as e:
            logger.warning("get_author_books {}: {}", author_id, e)
            await callback.message.answer("Ошибка при загрузке книг автора. Попробуйте позже.")
            return

        author_name = parsed_name or f"Автор #{author_id}"
        await state.update_data(author_id=author_id, author_name=author_name)

        page_hits = hits[:10]
        real_has_next = has_next or (len(hits) > 10)
        text = format_author_books(page_hits, author_name, page=1)
        kb = search_results_kb(page_hits, page=1, has_next=real_has_next, kind="author", target_id=author_id)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)

    @router.callback_query(DownloadCb.filter())
    async def handle_download(callback: CallbackQuery, callback_data: DownloadCb, bot: Bot) -> None:
        if not auth.is_authorized(callback.from_user.id):
            await callback.answer("Сначала введите /start.", show_alert=True)
            return
        await callback.answer("⏳ Скачиваю…")
        book_id = callback_data.book_id
        fmt = callback_data.fmt
        try:
            file = await provider.download(book_id, fmt)
        except ProviderError as e:
            logger.warning("download {}/{}: {}", book_id, fmt, e)
            await callback.message.answer("Ошибка при скачивании. Попробуйте другой формат.")
            return

        if len(file.content) > settings.max_file_size_bytes:
            url = provider.download_url(book_id, fmt)
            await callback.message.answer(
                f"Файл больше {settings.max_file_size_mb} МБ — скачайте напрямую:\n{url}"
            )
            return

        doc = BufferedInputFile(file.content, filename=file.filename)
        await bot.send_document(callback.message.chat.id, doc)

    @router.callback_query(WatchCb.filter())
    async def handle_watch(callback: CallbackQuery, callback_data: WatchCb, state: FSMContext) -> None:
        if not auth.is_authorized(callback.from_user.id):
            await callback.answer("Сначала введите /start.", show_alert=True)
            return
        user_id = callback.from_user.id

        if callback_data.action == "del":
            deleted = await storage.delete_watch(int(callback_data.target), user_id)
            await callback.answer("Подписка удалена." if deleted else "Уже удалена.")
            watches = await storage.list_watches(user_id)
            await callback.message.edit_text(
                format_watches(watches), parse_mode="HTML", reply_markup=watches_kb(watches)
            )
            return

        if not watchable:
            await callback.answer("Подписки недоступны.", show_alert=True)
            return
        kind: WatchKind | None = {"s": "series", "a": "author", "q": "query"}.get(callback_data.action)
        if kind is None:
            await callback.answer()
            return
        target = callback_data.target
        if kind == "query" and not target:  # длинный запрос не влез в callback — берём из FSM
            data = await state.get_data()
            target = (data.get("query") or "").strip()
            if not target:
                await callback.answer("Начните поиск заново — отправьте запрос текстом.", show_alert=True)
                return

        await callback.answer("⏳ Подписываю…")
        try:
            snapshot = await provider.watch_entries(kind, target)
        except ProviderError as e:
            logger.warning("watch_entries {}/{}: {}", kind, target, e)
            await callback.message.answer("Не удалось создать подписку. Попробуйте позже.")
            return
        if not snapshot.complete:
            await callback.message.answer(
                "По этой цели слишком много книг — подпишитесь на серию или конкретного автора."
            )
            return

        label = target if kind == "query" else (snapshot.label or f"#{target}")
        jitter = 0.9 + 0.2 * random.random()
        next_check_at = int(time.time() + settings.watch_interval_hours * 3600 * jitter)
        watch_id = await storage.add_watch(
            user_id, callback.message.chat.id, kind, target, label, snapshot.entries, next_check_at
        )
        if watch_id is None:
            await callback.message.answer("Такая подписка уже есть — /watches.")
            return
        kind_word = {"series": "серией", "author": "автором", "query": "запросом"}[kind]
        await callback.message.answer(
            f"✅ Слежу за {kind_word} <b>«{escape(label)}»</b> — сейчас книг: {len(snapshot.entries)}.\n"
            "Пришлю уведомление, когда появится новая. Подписки: /watches",
            parse_mode="HTML",
        )

    return router


# ----------------------------------------------------------------- helpers


async def _do_search(
    message: Message,
    state: FSMContext,
    provider: BookProvider,
    query: str,
    page: int,
    edit: bool,
    watch_query: bool = False,
) -> None:
    try:
        hits, has_next = await provider.search(query, page=page)
        logger.debug("search {!r} p{} → {} hits, has_next={}", query, page, len(hits), has_next)
    except ProviderError as e:
        logger.warning("search {!r} p{}: {}", query, page, e)
        await message.answer("Ошибка поиска. Попробуйте позже.")
        return

    text = format_search_results(hits, query, page)
    kb = search_results_kb(
        hits[:10], page=page, has_next=has_next, kind="search", watch_query=query if watch_query else None
    )

    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _do_author(
    message: Message,
    state: FSMContext,
    provider: BookProvider,
    author_id: str,
    author_name: str,
    page: int,
    edit: bool,
) -> None:
    try:
        hits, has_next, _ = await provider.get_author_books(author_id, page=page)
    except ProviderError as e:
        logger.warning("get_author_books {}: {}", author_id, e)
        await message.answer("Ошибка при загрузке книг. Попробуйте позже.")
        return

    page_hits = hits[(page - 1) * 10 : page * 10]
    real_has_next = has_next or (page * 10 < len(hits))
    text = format_author_books(page_hits, author_name, page)
    kb = search_results_kb(page_hits, page=page, has_next=real_has_next, kind="author", target_id=author_id)

    if edit:
        await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)
