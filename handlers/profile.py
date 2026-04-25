
#handlers/profile.py

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.types import Message
from config import config
from database.db import get_user, get_active_subscription, count_referrals
from keyboards.kb import (
    profile_keyboard,
    setup_platform_keyboard,
    setup_back_keyboard,
)
from locales.texts import t
from utils.helpers import format_datetime, days_left

logger = logging.getLogger(__name__)
router = Router()

_PHOTO_FILE_IDS: dict[str, str] = {}
PHOTOS = {
    "profile": "assets/profile.jpg",
}

SETUP_PLATFORMS = {"android", "ios", "windows", "macos", "linux", "tv"}

async def _send_profile_photo(callback, text, kb):
    cached_id = _PHOTO_FILE_IDS.get("profile")
    
    if cached_id:
        photo = cached_id
    else:
        photo = FSInputFile(PHOTOS["profile"])

    try:
        msg = await callback.message.edit_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=kb,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        msg = await callback.message.answer_photo(
            photo=photo, caption=text, reply_markup=kb, parse_mode="HTML",
        )
    
    # Сохраняем file_id если ещё не кэширован
    if not cached_id and msg and hasattr(msg, "photo") and msg.photo:
        _PHOTO_FILE_IDS["profile"] = msg.photo[-1].file_id

async def _build_profile_text(user: dict, lang: str) -> str:
    sub = await get_active_subscription(user["id"])
    referrals = await count_referrals(user["id"])

    if sub:
        expires = format_datetime(sub["expires_at"], lang)
        d_left = days_left(sub["expires_at"])
        sub_info = t("sub_active", lang, expires=expires, days_left=d_left,
                     limit=sub["devices_limit"], sub_link=sub["sub_link"])
    else:
        sub_info = t("sub_none", lang)

    return t(
        "profile", lang,
        user_id=user["id"],
        profile_key=user["profile_key"],
        balance=f"{user['balance']:.2f}",
        referrals=referrals,
        sub_info=sub_info,
    )

@router.callback_query(F.data == "menu:profile")
async def show_profile(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    lang = user.get("language", "ru")
    text = await _build_profile_text(user, lang)
    kb = profile_keyboard(lang)
    await _send_profile_photo(callback, text, kb)
    await callback.answer()

@router.callback_query(F.data == "menu:devices")
async def show_devices(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    lang = user.get("language", "ru")
    sub = await get_active_subscription(user["id"])
    if not sub:
        await callback.answer(t("no_sub_for_devices", lang), show_alert=True)
        return
    text = (
        f"📱 <b>{'Устройства' if lang == 'ru' else 'Devices'}</b>\n\n"
        f"{'Текущий лимит' if lang == 'ru' else 'Current limit'}: "
        f"<b>{sub['devices_limit']}</b>\n"
        f"{'Максимум' if lang == 'ru' else 'Maximum'}: "
        f"<b>{config.MAX_DEVICES + config.EXTRA_DEVICES}</b>"
    )
    from keyboards.kb import confirm_purchase_keyboard, back_keyboard
    from locales.texts import t as _t
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_t("extra_devices_btn", lang), callback_data="buy:extra_devices")],
        [InlineKeyboardButton(text=_t("btn_back", lang), callback_data="menu:profile")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu:change_lang")
async def change_language(callback: CallbackQuery):
    from keyboards.kb import lang_keyboard
    try:
        await callback.message.edit_text(
            "🌍 Выберите язык / Choose language:",
            reply_markup=lang_keyboard(),
        )
    except Exception:
        try:
            await callback.message.edit_caption(
                caption="🌍 Выберите язык / Choose language:",
                reply_markup=lang_keyboard(),
            )
        except Exception:
            await callback.message.answer(
                "🌍 Выберите язык / Choose language:",
                reply_markup=lang_keyboard(),
            )
    await callback.answer()

@router.callback_query(F.data == "setup:choose_platform")
async def setup_choose_platform(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    text = t("setup_choose_platform", lang)
    kb = setup_platform_keyboard(lang)

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await callback.answer()

@router.callback_query(F.data.startswith("setup:") & ~F.data.in_({"setup:choose_platform"}))
async def setup_platform_detail(callback: CallbackQuery):
    platform = callback.data.split(":")[1]

    if platform not in SETUP_PLATFORMS:
        await callback.answer()
        return

    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    text_key = f"setup_{platform}"
    text = t(text_key, lang)
    kb = setup_back_keyboard(lang)

    try:
        await callback.message.edit_text(
            text, reply_markup=kb, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                text, reply_markup=kb, parse_mode="HTML",
                disable_web_page_preview=True,
            )

    await callback.answer()