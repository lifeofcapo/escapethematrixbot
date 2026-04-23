from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, CallbackQuery, Message
from typing import Callable, Awaitable, Any
from config import config
from database.db import get_user


class ChannelCheckMiddleware(BaseMiddleware):
    """
    Checks if user has subscribed to the required channel.
    Skips check for language selection and check_sub callbacks.
    """

    SKIP_CALLBACKS = {"check_sub", "lang:ru", "lang:en"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[Any]],
        event: TelegramObject,
        data: dict,
    ) -> Any:
        # Determine user id and skip logic
        user = None
        if isinstance(event, CallbackQuery):
            user = event.from_user
            if event.data in self.SKIP_CALLBACKS or (event.data or "").startswith("lang:"):
                return await handler(event, data)
        elif isinstance(event, Message):
            user = event.from_user
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)

        if user is None:
            return await handler(event, data)
        # Check DB — if user not yet registered, skip (start handler will deal)
        db_user = await get_user(user.id)
        if not db_user:
            return await handler(event, data)
        # Check language set
        if not db_user.get("language"):
            return await handler(event, data)
        # Check channel membership
        bot: Bot = data["bot"]
        try:
            member = await bot.get_chat_member(
                chat_id=config.REQUIRED_CHANNEL_ID,
                user_id=user.id,
            )
            if member.status in ("left", "kicked", "banned"):
                raise Exception("not subscribed")
        except Exception:
            from keyboards.kb import subscribe_keyboard
            from locales.texts import t
            lang = db_user.get("language", "ru")
            text = t("subscribe_required", lang)
            kb = subscribe_keyboard(lang)
            if isinstance(event, CallbackQuery):
                await event.answer()
                await event.message.answer(text, reply_markup=kb)
            elif isinstance(event, Message):
                await event.answer(text, reply_markup=kb)
            return  # stop propagation

        return await handler(event, data)