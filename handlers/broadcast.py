import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from config import config
from database.db import get_pool
from keyboards.kb import broadcast_confirm_keyboard
from locales.texts import t

logger = logging.getLogger(__name__)
router = Router()
_BROADCAST_DELAY = 0.04


class BroadcastStates(StatesGroup):
    waiting_content = State()


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in config.ADMIN_IDS


@router.message(IsAdmin(), Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    await message.answer(
        "📢 Отправьте текст или фото для рассылки.\n"
        "Для отмены — /cancel"
    )
    await state.set_state(BroadcastStates.waiting_content)

@router.message(IsAdmin(), Command("cancel"), BroadcastStates.waiting_content)
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🚫 Рассылка отменена. Бот работает в обычном режиме.")

@router.message(IsAdmin(), BroadcastStates.waiting_content, F.photo)
async def broadcast_photo_received(message: Message, state: FSMContext):
    photo = message.photo[-1]
    caption = message.caption or ""

    await state.update_data(
        type="photo",
        photo_id=photo.file_id,
        caption=caption,
    )

    # Предпросмотр
    await message.answer_photo(
        photo=photo.file_id,
        caption=caption,
        parse_mode="HTML",
    )
    await message.answer(
        t("broadcast_preview", "ru"),
        reply_markup=broadcast_confirm_keyboard("ru", broadcast_id="pending"),
    )


@router.message(IsAdmin(), BroadcastStates.waiting_content, F.text)
async def broadcast_text_received(message: Message, state: FSMContext):
    text = message.text or ""

    await state.update_data(
        type="text",
        text=text,
    )
    await message.answer(text, parse_mode="HTML")
    await message.answer(
        t("broadcast_preview", "ru"),
        reply_markup=broadcast_confirm_keyboard("ru", broadcast_id="pending"),
    )

@router.callback_query(IsAdmin(), F.data == "broadcast:send:pending")
async def broadcast_send(callback: CallbackQuery, bot: Bot, state: FSMContext):
    data = await state.get_data()
    if not data:
        await callback.answer("Рассылка не найдена.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("broadcast_started", "ru"))
    await callback.answer()

    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT id FROM users")
    user_ids = [row["id"] for row in rows]

    sent = 0
    failed = 0
    is_photo = data.get("type") == "photo"

    for uid in user_ids:
        try:
            if is_photo:
                await bot.send_photo(
                    chat_id=uid,
                    photo=data["photo_id"],
                    caption=data.get("caption", ""),
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=data["text"],
                    parse_mode="HTML",
                )
            sent += 1
            await asyncio.sleep(_BROADCAST_DELAY)

        except TelegramRetryAfter as e:
            logger.warning(f"Broadcast: flood control, sleeping {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                if is_photo:
                    await bot.send_photo(
                        chat_id=uid,
                        photo=data["photo_id"],
                        caption=data.get("caption", ""),
                        parse_mode="HTML",
                    )
                else:
                    await bot.send_message(
                        chat_id=uid,
                        text=data["text"],
                        parse_mode="HTML",
                    )
                sent += 1
            except Exception as retry_e:
                logger.warning(f"Broadcast retry failed for {uid}: {retry_e}")
                failed += 1

        except (TelegramForbiddenError, TelegramBadRequest):
            # если пользователь заблокировал бота
            failed += 1

        except Exception as e:
            logger.warning(f"Broadcast failed for {uid}: {e}")
            failed += 1

    await bot.send_message(
        callback.from_user.id,
        t("broadcast_done", "ru", sent=sent, failed=failed),
    )
    await state.clear()

@router.callback_query(IsAdmin(), F.data == "broadcast:cancel:pending")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("broadcast_cancelled", "ru"))
    await state.clear()
    await callback.answer()