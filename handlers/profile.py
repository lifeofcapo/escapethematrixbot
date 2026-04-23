from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto
from database.db import get_user, get_active_subscription, count_referrals
from keyboards.kb import main_menu, back_keyboard, profile_keyboard, lang_keyboard
from locales.texts import t
from utils.helpers import format_datetime, days_left
from services.xui import get_online_count

router = Router()

PROFILE_PHOTO = "assets/profile.jpg"


async def _build_profile_text(user: dict, lang: str, with_online: bool = False) -> str:
    """Build profile text. with_online=True делает запрос к xui для онлайн-устройств."""
    sub = await get_active_subscription(user["id"])
    referrals = await count_referrals(user["id"])

    if sub:
        expires = format_datetime(sub["expires_at"], lang)
        d_left = days_left(sub["expires_at"])

        if with_online:
            online = await get_online_count(sub["xui_email"])
            if online is not None:
                devices_str = (
                    f"{online} из {sub['devices_limit']} подключено"
                    if lang == "ru"
                    else f"{online} of {sub['devices_limit']} connected"
                )
            else:
                devices_str = str(sub["devices_limit"])
        else:
            # Без запроса к xui — показываем только лимит
            devices_str = str(sub["devices_limit"])

        sub_info = t(
            "sub_active", lang,
            expires=expires,
            days_left=d_left,
            limit=devices_str,
            sub_link=sub["sub_link"],
        )
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
    text = await _build_profile_text(user, lang, with_online=False)
    photo = FSInputFile(PROFILE_PHOTO)
    kb = profile_keyboard(lang)

    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=kb,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=kb,
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data == "menu:devices")
async def show_devices(callback: CallbackQuery):
    """
    Кнопка 'Устройства' — только здесь делаем запрос к xui.
    Показывает актуальное количество подключённых устройств.
    """
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return

    lang = user.get("language", "ru")
    await callback.answer(
        "⏳ Запрашиваем данные..." if lang == "ru" else "⏳ Fetching data...",
        show_alert=False,
    )

    text = await _build_profile_text(user, lang, with_online=True)
    photo = FSInputFile(PROFILE_PHOTO)
    kb = profile_keyboard(lang)

    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"),
            reply_markup=kb,
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.callback_query(F.data == "menu:change_lang")
async def change_language_menu(callback: CallbackQuery):
    await callback.message.edit_caption(
        caption="🌍 Выберите язык / Choose language:",
        reply_markup=lang_keyboard(),
    ) if callback.message.photo else await callback.message.edit_text(
        "🌍 Выберите язык / Choose language:",
        reply_markup=lang_keyboard(),
    )
    await callback.answer()