import json
from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, CallbackQuery, Message
from typing import Callable, Awaitable, Any
from config import config
from database.db import get_user

CACHE_TTL = 360 


class ChannelCheckMiddleware(BaseMiddleware):
    SKIP_CALLBACKS = {"check_sub"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        user = None
        if isinstance(event, CallbackQuery):
            user = event.from_user
            if (event.data or "").startswith("lang:") or event.data in self.SKIP_CALLBACKS:
                return await handler(event, data)
        elif isinstance(event, Message):
            user = event.from_user
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)

        if user is None:
            return await handler(event, data)

        db_user = await get_user(user.id)
        if not db_user or not db_user.get("language"):
            return await handler(event, data)

        bot: Bot = data["bot"]
        cache_key = f"chan_check:{user.id}"

        # Проверяем Redis-кэш
        is_member = None
        storage = data.get("fsm_storage")  # получаем storage из данных диспетчера

        # Пробуем получить redis напрямую из storage
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            if isinstance(storage, RedisStorage):
                cached = await storage.redis.get(cache_key)
                if cached is not None:
                    is_member = json.loads(cached)
        except Exception:
            pass

        if is_member is None:
            try:
                member = await bot.get_chat_member(
                    chat_id=config.REQUIRED_CHANNEL_ID,
                    user_id=user.id,
                )
                is_member = member.status not in ("left", "kicked", "banned")
            except Exception:
                is_member = False

            # Сохраняем в Redis если доступен
            try:
                from aiogram.fsm.storage.redis import RedisStorage
                if isinstance(storage, RedisStorage):
                    await storage.redis.set(cache_key, json.dumps(is_member), ex=CACHE_TTL)
            except Exception:
                pass

        if not is_member:
            from keyboards.kb import subscribe_keyboard
            from locales.texts import t
            lang = db_user.get("language", "ru")
            if isinstance(event, CallbackQuery):
                await event.answer()
                await event.message.answer(t("subscribe_required", lang), reply_markup=subscribe_keyboard(lang))
            elif isinstance(event, Message):
                await event.answer(t("subscribe_required", lang), reply_markup=subscribe_keyboard(lang))
            return

        return await handler(event, data)