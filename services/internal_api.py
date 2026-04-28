import asyncio
import hashlib
import hmac
import json
import logging
import os

from aiohttp import web
from database.db import (
    get_active_subscription, get_balance, get_payment_by_provider_id,
    get_user, count_referrals, mark_payment_paid, update_balance,
)
from utils.helpers import days_left
from config import config

logger = logging.getLogger(__name__)

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")
PORT = int(os.getenv("INTERNAL_API_PORT", "8000"))

_bot = None


def _auth(request: web.Request) -> bool:
    return request.headers.get("X-Internal-Secret") == INTERNAL_SECRET

async def handle_profile(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "forbidden"}, status=403)

    user_id_str = request.match_info.get("user_id", "")
    try:
        user_id = int(user_id_str)
    except ValueError:
        return web.json_response({"error": "invalid_id"}, status=400)

    user = await get_user(user_id)
    if not user:
        return web.json_response({"error": "not_found"}, status=404)

    sub = await get_active_subscription(user_id)
    referrals = await count_referrals(user_id)
    balance = await get_balance(user_id)

    sub_data = None
    if sub:
        d_left = days_left(sub["expires_at"])
        expires_iso = (
            sub["expires_at"].isoformat()
            if hasattr(sub["expires_at"], "isoformat")
            else str(sub["expires_at"])
        )
        sub_data = {
            "status": "active" if d_left > 0 else "expired",
            "plan": sub["plan"],
            "expires_at": expires_iso,
            "days_left": d_left,
            "devices_limit": sub["devices_limit"],
            "sub_link": sub["sub_link"],
            "region": sub.get("region", "fi"),
        }

    return web.json_response({
        "user_id": user["id"],
        "username": user.get("username"),
        "profile_key": user["profile_key"],
        "balance": float(balance),
        "referrals": referrals,
        "language": user.get("language", "ru"),
        "subscription": sub_data,
    })

async def handle_yookassa_webhook(request: web.Request) -> web.Response:
    """
    YooKassa шлёт POST с JSON-событием.
    Документация: https://yookassa.ru/developers/using-api/webhooks
    """
    try:
        body = await request.read()
        data = json.loads(body)
    except Exception as e:
        logger.error(f"YooKassa webhook: failed to parse body: {e}")
        return web.Response(status=400)

    event = data.get("event")
    obj = data.get("object", {})
    payment_id = obj.get("id")

    logger.info(f"YooKassa webhook received: event={event} payment_id={payment_id}")

    if event != "payment.succeeded":
        # Остальные события (refund и т.д.) пока игнорируем, но отвечаем 200
        return web.Response(status=200)

    if not payment_id:
        logger.error("YooKassa webhook: no payment id in object")
        return web.Response(status=400)

    # Достаём user_id из metadata платежа
    metadata = obj.get("metadata", {})
    try:
        user_id = int(metadata.get("user_id", 0))
    except (ValueError, TypeError):
        user_id = 0

    if not user_id:
        logger.error(f"YooKassa webhook: no user_id in metadata for payment {payment_id}")
        return web.Response(status=200)  # 200 чтобы YooKassa не ретраила

    amount = float(obj.get("amount", {}).get("value", 0))
    if amount <= 0:
        logger.error(f"YooKassa webhook: invalid amount for payment {payment_id}")
        return web.Response(status=200)

    # Идемпотентность — не начисляем дважды
    payment = await get_payment_by_provider_id(payment_id)
    if not payment:
        logger.warning(f"YooKassa webhook: payment {payment_id} not found in DB, skipping")
        return web.Response(status=200)

    if payment["status"] == "paid":
        logger.info(f"YooKassa webhook: payment {payment_id} already processed, skipping")
        return web.Response(status=200)

    # Начисляем баланс
    await mark_payment_paid(payment_id)
    await update_balance(user_id, amount)
    balance = await get_balance(user_id)

    # Реферальный бонус
    user = await get_user(user_id)
    if user and user.get("referred_by"):
        bonus = round(amount * config.REFERRAL_BONUS_PERCENT / 100.0, 2)
        await update_balance(user["referred_by"], bonus)
        logger.info(f"Referral bonus {bonus}₽ → user {user['referred_by']}")

    # Уведомляем пользователя
    if _bot:
        from locales.texts import t
        lang = user.get("language", "ru") if user else "ru"
        try:
            await _bot.send_message(
                user_id,
                t("balance_topped", lang, amount=amount, balance=f"{balance:.2f}"),
            )
        except Exception as e:
            logger.error(f"YooKassa webhook: failed to notify user {user_id}: {e}")

    logger.info(f"YooKassa webhook: +{amount}₽ → user {user_id}, new balance={balance:.2f}")
    return web.Response(status=200)

async def start_internal_api(bot=None):
    """Запускается как asyncio.Task внутри основного event loop бота."""
    global _bot
    _bot = bot

    app = web.Application()
    app.router.add_get("/internal/profile/{user_id}", handle_profile)
    app.router.add_post("/webhook/yookassa", handle_yookassa_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Internal API started on 0.0.0.0:{PORT}")
    logger.info("YooKassa webhook endpoint: /webhook/yookassa")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()