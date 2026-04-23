"""
services/internal_api.py

Лёгкий HTTP-сервер (aiohttp) для внутреннего использования Next.js.
Запускается вместе с ботом и слушает на localhost:8000.
Доступен только изнутри — не открывается наружу.

Добавь в bot.py:
    from services.internal_api import start_internal_api
    ...
    asyncio.create_task(start_internal_api())
"""
import logging
import os
from aiohttp import web
from database.db import get_user, get_active_subscription, count_referrals, get_balance
from utils.helpers import days_left

logger = logging.getLogger(__name__)

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change_me_in_production")
PORT = int(os.getenv("INTERNAL_API_PORT", "8000"))


def _auth(request: web.Request) -> bool:
    """Проверяет X-Internal-Secret заголовок."""
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
        expires_iso = sub["expires_at"].isoformat() if hasattr(sub["expires_at"], "isoformat") else str(sub["expires_at"])
        sub_data = {
            "status": "active" if d_left > 0 else "expired",
            "plan": sub["plan"],
            "expires_at": expires_iso,
            "days_left": d_left,
            "devices_limit": sub["devices_limit"],
            "sub_link": sub["sub_link"],
            "region": "fi",  # TODO: когда добавишь мульти-регион — брать из sub
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


async def start_internal_api():
    """Запускается как asyncio.Task внутри основного event loop бота."""
    app = web.Application()
    app.router.add_get("/internal/profile/{user_id}", handle_profile)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=PORT)
    await site.start()
    logger.info(f"Internal API started on 127.0.0.1:{PORT}")

    # Держим сервер живым — task отменится при shutdown бота
    import asyncio
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()