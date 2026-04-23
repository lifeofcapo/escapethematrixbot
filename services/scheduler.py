"""
services/scheduler.py

Планировщик уведомлений об истечении подписки.
Запускается как фоновая задача asyncio вместе с ботом.
Проверяет каждые 6 часов подписки, истекающие через 7, 3 и 1 день,
и отправляет уведомление каждому пользователю один раз.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot

from database.db import get_pool
from locales.texts import t
from keyboards.kb import topup_method_keyboard

logger = logging.getLogger(__name__)

NOTIFY_DAYS = [7, 3, 1]
CHECK_INTERVAL = 6 * 3600


async def _get_expiring_subscriptions(days: int) -> list[dict]:
    """
    Возвращает подписки, которые истекают ровно через `days` дней
    (окно ±12 часов от полуночи целевого дня, чтобы не пропустить).
    """
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(days=days) - timedelta(hours=12)
    window_end   = now + timedelta(days=days) + timedelta(hours=12)

    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                s.id        AS sub_id,
                s.user_id,
                s.expires_at,
                s.plan,
                u.language,
                u.balance
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = TRUE
              AND s.expires_at BETWEEN $1 AND $2
        """, window_start, window_end)
    return [dict(r) for r in rows]


async def _already_notified(sub_id: int, days: int) -> bool:
    """Проверяет, было ли уже отправлено уведомление за X дней для этой подписки."""
    async with get_pool().acquire() as conn:
        val = await conn.fetchval("""
            SELECT 1 FROM expiry_notifications
            WHERE sub_id = $1 AND notify_days = $2
        """, sub_id, days)
    return val is not None


async def _mark_notified(sub_id: int, days: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("""
            INSERT INTO expiry_notifications (sub_id, notify_days, sent_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT DO NOTHING
        """, sub_id, days)


async def _send_notification(bot: Bot, user_id: int, lang: str,
                              days: int, balance: float) -> None:
    text = t("expiry_notify", lang, days=days, balance=f"{balance:.2f}")
    try:
        await bot.send_message(
            user_id,
            text,
            reply_markup=topup_method_keyboard(lang),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info(f"Expiry notification ({days}d) sent to user {user_id}")
    except Exception as e:
        logger.warning(f"Failed to send expiry notification to {user_id}: {e}")


async def run_scheduler(bot: Bot) -> None:
    """Основной цикл планировщика. Запускается как asyncio.Task."""
    logger.info("Scheduler started")
    while True:
        try:
            await _check_and_notify(bot)
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL)


async def _check_and_notify(bot: Bot) -> None:
    for days in NOTIFY_DAYS:
        subs = await _get_expiring_subscriptions(days)
        for sub in subs:
            if await _already_notified(sub["sub_id"], days):
                continue
            await _send_notification(
                bot,
                user_id=sub["user_id"],
                lang=sub["language"],
                days=days,
                balance=float(sub["balance"]),
            )
            await _mark_notified(sub["sub_id"], days)
            await asyncio.sleep(0.05)  # не спамим Telegram API
        if subs:
            logger.info(f"Notified {len(subs)} users about expiry in {days} days")