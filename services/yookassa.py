import uuid
import aiohttp
import logging
from config import config

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3/payments"

async def create_yookassa_payment(
    amount_rub: float,
    description: str,
    metadata: dict,
    return_url: str = "https://t.me/EscapeTheMatrixVPNBot",
) -> dict | None:
    """
    Create a YooKassa payment.
    Returns dict with {payment_id, confirmation_url} or None.
    """
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
        "metadata": metadata,
    }

    auth = aiohttp.BasicAuth(config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET)

    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            YOOKASSA_API,
            json=payload,
            auth=auth,
            headers={"Idempotence-Key": idempotence_key},
        )
        if resp.status in (200, 201):
            data = await resp.json()
            return {
                "payment_id": data["id"],
                "confirmation_url": data["confirmation"]["confirmation_url"],
                "status": data["status"],
            }
        text = await resp.text()
        logger.error(f"YooKassa error {resp.status}: {text}")
    return None


async def check_yookassa_payment(payment_id: str) -> str | None:
    """Returns payment status: pending / waiting_for_capture / succeeded / canceled"""
    auth = aiohttp.BasicAuth(config.YOOKASSA_SHOP_ID, config.YOOKASSA_SECRET)
    async with aiohttp.ClientSession() as s:
        resp = await s.get(f"{YOOKASSA_API}/{payment_id}", auth=auth)
        if resp.status == 200:
            data = await resp.json()
            return data.get("status")
    return None