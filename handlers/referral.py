from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from config import config
from database.db import get_user, count_referrals
from keyboards.kb import back_keyboard
from locales.texts import t

router = Router()


@router.callback_query(F.data == "menu:referral")
async def show_referral(callback: CallbackQuery, bot: Bot):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return

    lang = user.get("language", "ru")
    referrals = await count_referrals(user["id"])

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user['id']}"

    text = t(
        "referral_header", lang,
        ref_link=ref_link,
        referrals=referrals,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=back_keyboard(lang, target="menu:profile"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            text,
            reply_markup=back_keyboard(lang, target="menu:profile"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    await callback.answer()  # FIX: был пропущен, спиннер крутился вечно