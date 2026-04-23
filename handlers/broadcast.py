"""
Admin broadcast handler.
Admins send a photo with caption → bot previews → confirms → sends to all users.
"""
import asyncio
import logging
import uuid

from aiogram import Router, F, Bot
from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery

from config import config
from database.db import get_pool
from keyboards.kb import broadcast_confirm_keyboard
from locales.texts import t

logger = logging.getLogger(__name__)
router = Router()

# Временное хранилище ожидающих рассылок: {broadcast_id: {admin_id, photo_id, caption}}
_pending: dict[str, dict] = {}


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user_id = event.from_user.id if isinstance(event, Message) else event.from_user.id
        return user_id in config.ADMIN_IDS

@router.message(IsAdmin(), F.photo)
async def admin_photo_broadcast(message: Message):
    lang = "ru"
    photo = message.photo[-1]  # наибольшее разрешение
    caption = message.caption or ""

    broadcast_id = str(uuid.uuid4())[:8]
    _pending[broadcast_id] = {
        "admin_id": message.from_user.id,
        "photo_id": photo.file_id,
        "caption": caption,
    }

    await message.answer_photo(
        photo=photo.file_id,
        caption=caption,
        parse_mode="HTML",
    )
    await message.answer(
        t("broadcast_preview", lang),
        reply_markup=broadcast_confirm_keyboard(lang, broadcast_id),
    )

@router.callback_query(IsAdmin(), F.data.startswith("broadcast:send:"))
async def broadcast_send(callback: CallbackQuery, bot: Bot):
    lang = "ru"
    broadcast_id = callback.data.split(":")[2]
    pending = _pending.pop(broadcast_id, None)

    if not pending:
        await callback.answer("Рассылка не найдена или уже отправлена.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("broadcast_started", lang))
    await callback.answer()

    # Получаем всех пользователей из БД
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT id FROM users")
    user_ids = [row["id"] for row in rows]

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_photo(
                chat_id=uid,
                photo=pending["photo_id"],
                caption=pending["caption"],
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)  # ~20 сообщений/сек, не превышаем лимит TG

    await bot.send_message(
        pending["admin_id"],
        t("broadcast_done", lang, sent=sent, failed=failed),
    )
@router.callback_query(IsAdmin(), F.data.startswith("broadcast:cancel:"))
async def broadcast_cancel(callback: CallbackQuery):
    lang = "ru"
    broadcast_id = callback.data.split(":")[2]
    _pending.pop(broadcast_id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("broadcast_cancelled", lang))
    await callback.answer()