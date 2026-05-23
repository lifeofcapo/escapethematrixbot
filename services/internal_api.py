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
from services.webhook_registry import mark_handled as _mark_webhook_handled

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
        return web.Response(status=200)

    if not payment_id:
        logger.error("YooKassa webhook: no payment id in object")
        return web.Response(status=400)

    metadata = obj.get("metadata", {})
    try:
        user_id = int(metadata.get("user_id", 0))
    except (ValueError, TypeError):
        user_id = 0

    if not user_id:
        logger.error(f"YooKassa webhook: no user_id in metadata for payment {payment_id}")
        return web.Response(status=200)

    amount = float(obj.get("amount", {}).get("value", 0))
    if amount <= 0:
        logger.error(f"YooKassa webhook: invalid amount for payment {payment_id}")
        return web.Response(status=200)

    payment = await get_payment_by_provider_id(payment_id)
    if not payment:
        logger.warning(f"YooKassa webhook: payment {payment_id} not found in DB, skipping")
        return web.Response(status=200)

    if payment["status"] == "paid":
        logger.info(f"YooKassa webhook: payment {payment_id} already processed, skipping")
        # Всё равно сигналим — на случай если таск ещё крутится
        _mark_webhook_handled(payment_id)
        return web.Response(status=200)

    await mark_payment_paid(payment_id)
    await update_balance(user_id, amount)
    balance = await get_balance(user_id)

    # Реферальный бонус
    user = await get_user(user_id)
    if user and user.get("referred_by"):
        bonus = round(amount * config.REFERRAL_BONUS_PERCENT / 100.0, 2)
        await update_balance(user["referred_by"], bonus)
        logger.info(f"Referral bonus {bonus}₽ → user {user['referred_by']}")

    # ↓ Сигналим polling-таску: вебхук сработал, можно выходить
    _mark_webhook_handled(payment_id)

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

async def handle_web_subscription(request: web.Request) -> web.Response:
    """
    POST /internal/web-subscription
    Body: { "user_id": 123, "days": 30, "amount": 100 }
    Header: X-Internal-Secret: ...
    """
    if not _auth(request):
        return web.json_response({"error": "forbidden"}, status=403)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_body"}, status=400)

    user_id = body.get("user_id")
    days = body.get("days")
    amount = body.get("amount")
    requested_region = body.get("region", "fi")  

    if not user_id or not days or not amount:
        return web.json_response({"error": "missing_params"}, status=400)

    try:
        user_id = int(user_id)
        days = int(days)
        amount = float(amount)
        region = "nl" if requested_region == "nl" else "fi"
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid_params"}, status=400)
    user = await get_user(user_id)
    if not user:
        return web.json_response({"error": "user_not_found"}, status=404)
    sub = await get_active_subscription(user_id)

    from services import xui 

    if sub and sub.get("xui_client_id") and sub.get("xui_email"):
        from datetime import datetime, timezone as tz, timedelta

        client_id = sub["xui_client_id"]
        email = sub["xui_email"]
        region = sub.get("region", "fi")

        expires_at = sub["expires_at"]
        base = expires_at if hasattr(expires_at, "timestamp") else datetime.fromisoformat(str(expires_at))
        if base.tzinfo is None:
            base = base.replace(tzinfo=tz.utc)

        now_utc = datetime.now(tz.utc)
        effective_base = max(base, now_utc)
        current_expire_ms = int(effective_base.timestamp() * 1000)

        ok = await xui.update_client_expiry(
            client_id=client_id,
            email=email,
            extra_days=days,
            current_expire_ms=current_expire_ms,
            region=region,
            devices_limit=sub.get("devices_limit", 4),
        )
        if not ok:
            logger.error(f"web-subscription: xui renew failed for user {user_id}")
            return web.json_response({"error": "xui_error"}, status=502)

        new_expiry = effective_base + timedelta(days=days)

        db = get_pool()
        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET expires_at = $1 WHERE id = $2",
                new_expiry, sub["id"]
            )

        logger.info(f"web-subscription: renewed {days}d for user {user_id}, new expiry={new_expiry}")
        return web.json_response({"ok": True, "action": "renew", "expires_at": new_expiry.isoformat()})

    else:
        import uuid as _uuid
        email = f"web{user_id}_{_uuid.uuid4().hex[:6]}"

        result = await xui.create_client(
            email=email,
            days=days,
            devices_limit=4,
            region=region,
        )

        if not result:
            logger.error(f"web-subscription: xui create_client failed for user {user_id}")
            return web.json_response({"error": "xui_error"}, status=502)

        client_id = result["client_id"]
        sub_link = result["sub_link"]

        from datetime import datetime, timezone as tz, timedelta
        now = datetime.now(tz.utc)
        expires_at = now + timedelta(days=days)

        from database.db import get_pool
        db = get_pool()
        async with db.acquire() as conn:
            await conn.execute(
                """INSERT INTO subscriptions
                   (user_id, xui_client_id, xui_email, sub_link, plan, devices_limit,
                    started_at, expires_at, is_active, region)
                   VALUES ($1, $2, $3, $4, 'web', 4, $5, $6, TRUE, $7)""",
                user_id, client_id, email, sub_link, now, expires_at, region
            )

        logger.info(f"web-subscription: created for user {user_id}, client_id={client_id}, region={region}")
        return web.json_response({
            "ok": True,
            "action": "new",
            "client_id": client_id,
            "sub_link": sub_link,
            "expires_at": expires_at.isoformat(),
        })

async def start_internal_api(bot=None):
    """Запускается как asyncio.Task внутри основного event loop бота."""
    global _bot
    _bot = bot

    app = web.Application()
    app.router.add_get("/internal/profile/{user_id}", handle_profile)
    app.router.add_post("/webhook/yookassa", handle_yookassa_webhook)
    app.router.add_post("/internal/web-subscription", handle_web_subscription)

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