import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

from config import config
from database.db import (
    get_user, get_pool,
    get_active_subscription,
    create_subscription,
)
from keyboards.kb import plans_keyboard, payment_success_keyboard, all_regions_label
from locales.texts import t
from services.xui import create_client, update_client_expiry, get_client
from utils.helpers import generate_sub_email

logger = logging.getLogger(__name__)
router = Router()

async def _is_trial_used(user_id: int) -> bool:
    async with get_pool().acquire() as conn:
        val = await conn.fetchval(
            "SELECT trial_used FROM users WHERE id = $1", user_id
        )
        return bool(val)


async def _mark_trial_used(user_id: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE users SET trial_used = TRUE WHERE id = $1", user_id
        )

@router.callback_query(F.data == "trial:start")
async def trial_start(callback: CallbackQuery, bot: Bot):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    # Уже использовал trial
    if await _is_trial_used(callback.from_user.id):
        await callback.answer(t("trial_already_used", lang), show_alert=True)
        return

    active_sub = await get_active_subscription(callback.from_user.id)

    try:
        if active_sub:
            # Есть активная подписка — добавляем 3 дня к ней
            await _extend_trial(bot, callback.from_user.id, lang, active_sub)
        else:
            # Нет подписки — создаём новую trial
            await _activate_trial(bot, callback.from_user.id, lang)

        await _mark_trial_used(callback.from_user.id)
        await callback.answer()

    except Exception as e:
        logger.error(
            f"Trial activation failed for user {callback.from_user.id}: {e}",
            exc_info=True,
        )
        await callback.answer(t("error_generic", lang), show_alert=True)

async def _activate_trial(bot: Bot, user_id: int, lang: str, region: str = "fi"):
    days = config.TRIAL_DAYS
    email = generate_sub_email(user_id)

    result = await create_client(
        email=email,
        days=days,
        devices_limit=config.MAX_DEVICES,
        region=region,
    )
    if not result:
        raise RuntimeError("XUI create_client failed")

    await create_subscription(
        user_id=user_id,
        xui_client_id=result["client_id"],
        xui_email=email,
        sub_link=result["sub_link"],
        plan="trial",
        days=days,
        devices_limit=config.MAX_DEVICES,
        region=region,
        is_trial=True,
    )

    region_str = all_regions_label(lang)
    await bot.send_message(
        user_id,
        t("trial_success", lang,
          days=days,
          region_label=region_str,
          sub_link=result["sub_link"]),
        parse_mode="HTML",
        reply_markup=payment_success_keyboard(lang),
    )

async def _extend_trial(bot: Bot, user_id: int, lang: str, sub: dict):
    days = config.TRIAL_DAYS
    email = sub["xui_email"]
    client_id = sub["xui_client_id"]
    region = sub.get("region", "fi")

    # Считаем текущий expire в мс (для update_client_expiry)
    expires_at = sub["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    effective_base = max(expires_at, now_utc)
    current_expire_ms = int(effective_base.timestamp() * 1000)

    ok = await update_client_expiry(
        client_id=client_id,
        email=email,
        extra_days=days,
        current_expire_ms=current_expire_ms,
        region=region,
        devices_limit=sub.get("devices_limit", config.MAX_DEVICES),
    )
    if not ok:
        raise RuntimeError("XUI update_client_expiry failed")

    # Обновляем БД
    new_expiry = effective_base + timedelta(days=days)
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE subscriptions SET expires_at = $1 WHERE id = $2",
            new_expiry, sub["id"],
        )

    from utils.helpers import format_datetime
    expires_fmt = format_datetime(new_expiry, lang)

    if lang == "ru":
        text = (
            f"🎁 <b>Пробный период активирован!</b>\n\n"
            f"К вашей подписке добавлено <b>{days} дня</b>.\n"
            f"📅 Новая дата истечения: <b>{expires_fmt}</b>"
        )
    else:
        text = (
            f"🎁 <b>Trial activated!</b>\n\n"
            f"<b>{days} days</b> added to your subscription.\n"
            f"📅 New expiry date: <b>{expires_fmt}</b>"
        )

    await bot.send_message(
        user_id,
        text,
        parse_mode="HTML",
        reply_markup=payment_success_keyboard(lang),
    )