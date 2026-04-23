from aiogram import Router, Bot, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, FSInputFile
from config import config
from database.db import get_user, create_user, count_referrals, get_balance, get_active_subscription
from keyboards.kb import (
    lang_keyboard, subscribe_keyboard, main_menu,
    back_keyboard, profile_keyboard, topup_method_keyboard, region_keyboard,
)
from locales.texts import t
from utils.helpers import generate_profile_key, format_datetime, days_left

router = Router()
PHOTOS = {
    "menu":    "assets/menu.png",
    "plans":   "assets/plans.jpg",
    "topup":   "assets/topup.png",
    "profile": "assets/profile.jpg",
}


async def _notify_admins_new_user(bot: Bot, user_id: int, full_name: str, username: str, lang: str):
    text = t(
        "admin_new_user", "ru",
        user_id=user_id,
        full_name=full_name,
        username=username or "—",
        user_lang=lang,
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


async def _send_main_menu(target, lang: str, profile_key: str, edit: bool = False):
    text = t("welcome", lang, profile_key=profile_key, site=config.SITE_URL)
    kb = main_menu(lang)
    photo = FSInputFile(PHOTOS["menu"])

    if edit and isinstance(target, CallbackQuery):
        try:
            await target.message.edit_media(
                media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
                reply_markup=kb,
            )
            return
        except Exception:
            pass
        try:
            await target.message.delete()
        except Exception:
            pass
        await target.message.answer_photo(
            photo=photo, caption=text, reply_markup=kb, parse_mode="HTML"
        )
        return

    if isinstance(target, Message):
        await target.answer_photo(
            photo=photo, caption=text, reply_markup=kb, parse_mode="HTML"
        )
    elif isinstance(target, CallbackQuery):
        await target.message.answer_photo(
            photo=photo, caption=text, reply_markup=kb, parse_mode="HTML"
        )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject = None):
    referred_by = None
    if command and command.args:
        try:
            referred_by = int(command.args)
            if referred_by == message.from_user.id:
                referred_by = None
        except ValueError:
            pass

    user = await get_user(message.from_user.id)
    if not user:
        await message.answer(
            t("choose_language", "ru"),
            reply_markup=lang_keyboard(referred_by=referred_by),
        )
        return

    lang = user.get("language", "ru")
    await _send_main_menu(message, lang, user["profile_key"])


@router.callback_query(F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    lang = parts[1]
    referred_by = int(parts[2]) if len(parts) > 2 else None

    user_id = callback.from_user.id
    existing = await get_user(user_id)
    is_new = not existing

    if not existing:
        key = generate_profile_key()
        await create_user(
            user_id=user_id,
            username=callback.from_user.username or "",
            full_name=callback.from_user.full_name or "",
            language=lang,
            profile_key=key,
            referred_by=referred_by,
        )
    else:
        from database.db import update_user_language
        await update_user_language(user_id, lang)

    if is_new:
        await _notify_admins_new_user(
            bot, user_id,
            full_name=callback.from_user.full_name or "",
            username=callback.from_user.username or "",
            lang=lang,
        )

    try:
        member = await bot.get_chat_member(
            chat_id=config.REQUIRED_CHANNEL_ID,
            user_id=user_id,
        )
        is_member = member.status not in ("left", "kicked", "banned")
    except Exception:
        is_member = False

    if not is_member:
        await callback.message.edit_text(
            t("subscribe_required", lang),
            reply_markup=subscribe_keyboard(lang),
        )
        return

    user = await get_user(user_id)
    await _send_main_menu(callback, lang, user["profile_key"], edit=True)
    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    lang = user.get("language", "ru") if user else "ru"

    try:
        member = await bot.get_chat_member(
            chat_id=config.REQUIRED_CHANNEL_ID,
            user_id=user_id,
        )
        is_member = member.status not in ("left", "kicked", "banned")
    except Exception:
        is_member = False

    if not is_member:
        await callback.answer(t("sub_not_found", lang), show_alert=True)
        return

    if not user:
        key = generate_profile_key()
        await create_user(
            user_id=user_id,
            username=callback.from_user.username or "",
            full_name=callback.from_user.full_name or "",
            language=lang,
            profile_key=key,
        )
        user = await get_user(user_id)

    await _send_main_menu(callback, lang, user["profile_key"], edit=True)
    await callback.answer()


@router.callback_query(F.data == "menu:back")
async def menu_back(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    await _send_main_menu(callback, lang, user["profile_key"], edit=True)
    await callback.answer()


@router.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        return
    lang = user.get("language", "ru")
    sub = await get_active_subscription(user["id"])
    referrals = await count_referrals(user["id"])

    if sub:
        expires = format_datetime(sub["expires_at"], lang)
        d_left = days_left(sub["expires_at"])
        sub_info = t("sub_active", lang, expires=expires, days_left=d_left,
                     limit=sub["devices_limit"], sub_link=sub["sub_link"])
    else:
        sub_info = t("sub_none", lang)

    await message.answer(
        t("profile", lang, user_id=user["id"], profile_key=user["profile_key"],
          balance=f"{user['balance']:.2f}", referrals=referrals, sub_info=sub_info),
        reply_markup=profile_keyboard(lang),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        return
    lang = user.get("language", "ru")
    balance = await get_balance(message.from_user.id)
    text = (
        f"💰 <b>Ваш баланс: {balance:.2f} ₽</b>" if lang == "ru"
        else f"💰 <b>Your balance: {balance:.2f} ₽</b>"
    )
    await message.answer(text, reply_markup=topup_method_keyboard(lang), parse_mode="HTML")


@router.message(Command("plans"))
async def cmd_plans(message: Message):
    """
    /plans → показываем выбор региона (не хардкодим Финляндию напрямую).
    """
    user = await get_user(message.from_user.id)
    if not user:
        return
    lang = user.get("language", "ru")
    text = (
        "🌍 <b>Выберите регион сервера:</b>"
        if lang == "ru"
        else "🌍 <b>Choose server region:</b>"
    )
    await message.answer(text, reply_markup=region_keyboard(lang), parse_mode="HTML")


@router.message(Command("referral"))
async def cmd_referral(message: Message, bot: Bot):
    user = await get_user(message.from_user.id)
    if not user:
        return
    lang = user.get("language", "ru")
    referrals = await count_referrals(user["id"])
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user['id']}"
    await message.answer(
        t("referral_header", lang, ref_link=ref_link, referrals=referrals),
        reply_markup=back_keyboard(lang, target="menu:back"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(Command("support"))
async def cmd_support(message: Message):
    user = await get_user(message.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    await message.answer(t("support_header", lang), parse_mode="HTML")