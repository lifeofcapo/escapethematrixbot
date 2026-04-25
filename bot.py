import asyncio
import logging
import sys
import signal

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, BotCommandScopeDefault

from config import config
from database.db import create_pool, init_db, close_pool
from middlewares.channel_check import ChannelCheckMiddleware
from handlers import start, profile, payment, support, referral, broadcast
from services.scheduler import run_scheduler
from services.internal_api import start_internal_api

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

logging.getLogger("aiogram").setLevel(logging.WARNING)

def _make_storage():
    redis_url = config.REDIS_URL
    if redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(redis_url)
            logger.info(f"FSM storage: Redis ({redis_url})")
            return storage
        except Exception as e:
            logger.warning(f"Redis unavailable ({e}), falling back to MemoryStorage")

    from aiogram.fsm.storage.memory import MemoryStorage
    logger.info("FSM storage: MemoryStorage (states lost on restart)")
    return MemoryStorage()

async def set_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start",    description="🏠 Главное меню"),
        BotCommand(command="profile",  description="👤 Мой профиль"),
        BotCommand(command="balance",  description="💰 Мой баланс"),
        BotCommand(command="plans",    description="💎 Тарифы"),
        BotCommand(command="referral", description="👥 Реферальная программа"),
        BotCommand(command="support",  description="🆘 Поддержка"),
        BotCommand(command="miniapp",  description="🌐 Мой профиль (Mini App)"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

async def main():
    await create_pool(config.DATABASE_URL)
    await init_db()
    logger.info("Database ready")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await set_commands(bot)

    storage = _make_storage()
    dp = Dispatcher(storage=storage)
    dp["fsm_storage"] = storage

    dp.message.middleware(ChannelCheckMiddleware())
    dp.callback_query.middleware(ChannelCheckMiddleware())

    # broadcast первым до channel_check, чтобы фото от админов не блокировались
    dp.include_router(broadcast.router)
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(referral.router)
    dp.include_router(payment.router)
    dp.include_router(support.router)
    stop_event = asyncio.Event()     # Флаг для graceful shutdown
    scheduler_task = asyncio.create_task(run_scheduler(bot)) # Планировщик уведомлений об истечении подписок
    internal_api_task = asyncio.create_task(start_internal_api()) 
    loop = asyncio.get_running_loop() # (SIGINT = Ctrl+C, SIGTERM = systemd stop)

    def _handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    logger.info("Starting polling (local dev mode, no webhook)...")
    await bot.delete_webhook(drop_pending_updates=True)

    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False)
    )

    await stop_event.wait()
    logger.info("Stopping...")
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    internal_api_task.cancel() # Останавливаем внутренний API
    try:
        await internal_api_task
    except asyncio.CancelledError:
        pass

    if hasattr(storage, "close"): # Закрываем соединения
        await storage.close()
    await close_pool()
    from services.xui import close_session as close_xui_session
    await close_xui_session()
    await bot.session.close()
    logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass