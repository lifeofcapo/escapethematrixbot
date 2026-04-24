from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_user
from locales.texts import t

router = Router()

SUPPORT_DIRECT_URL = "https://t.me/EscapeTheMatrix_VPN?direct"
SUPPORT_USERNAME = "@lifeofcapo"

def support_keyboard(lang: str) -> InlineKeyboardMarkup:
    write_btn = "✍️ Написать в поддержку" if lang == "ru" else "✍️ Contact support"
    back_btn = "◀️ Назад" if lang == "ru" else "◀️ Back"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=write_btn, url=SUPPORT_DIRECT_URL)],
        [InlineKeyboardButton(text=back_btn, callback_data="menu:back")],
    ])


@router.callback_query(F.data == "menu:support")
async def show_support(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    text = t("support_header", lang)
    kb = support_keyboard(lang)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

    await callback.answer()