import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from bot import auth
from bot.handlers import setup
from config import settings
from providers.flibusta import FlibustaProvider


async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, colorize=True, format="{time:HH:mm:ss} | {level} | {message}")

    auth.load()
    provider = FlibustaProvider()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup(provider))

    logger.info("Starting FlibustaBot (long-polling)…")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await provider.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
