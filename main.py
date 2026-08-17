import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from bot import announce, auth
from bot.handlers import FLIBUSTA, LIBGEN, setup
from config import settings
from providers.flibusta import FlibustaProvider
from providers.libgen import LibgenProvider
from storage import Storage
from watcher import Watcher


async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, colorize=True, format="{time:HH:mm:ss} | {level} | {message}")

    auth.load()
    # Флибуста — источник по умолчанию и единственный с подписками; libgen подключается
    # кнопкой под результатами поиска. Порядок важен: первый ключ — дефолт.
    flibusta = FlibustaProvider()
    providers = {FLIBUSTA: flibusta, LIBGEN: LibgenProvider()}
    storage = await Storage.open(settings.db_path)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup(providers, storage))
    watcher = Watcher(bot, flibusta, storage)  # подписки — только Флибуста

    logger.info("Starting FlibustaBot (long-polling)…")
    if settings.send_announcement:
        try:
            await announce.send_pending_announcement(bot, storage)
        except Exception as e:  # анонс не должен мешать запуску бота
            logger.error("announcement failed: {}", e)
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=["message", "callback_query"]), name="polling"
    )
    watcher_task = asyncio.create_task(watcher.run(), name="watcher")
    try:
        # Завершился любой из двух (штатный стоп polling'а или падение watcher'а) —
        # гасим второй; исключение пробрасываем, чтобы systemd/docker перезапустил процесс.
        done, pending = await asyncio.wait({polling_task, watcher_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        await storage.close()
        for p in providers.values():
            await p.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
