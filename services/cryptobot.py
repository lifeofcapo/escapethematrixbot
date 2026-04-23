"""CryptoBot (https://t.me/CryptoBot) payment integration."""
import aiohttp
import logging
from config import config

logger = logging.getLogger(__name__)

API = config.CRYPTOBOT_API_URL
HEADERS = {"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN}

# Supported assets
CRYPTO_ASSETS = {
    "USDT":   "💵 USDT",
    "BTC":    "₿ Bitcoin",
    "ETH":    "Ξ Ethereum",
    "BNB":    "🟡 BNB",
    "SOL":    "◎ Solana",
    "TON":    "💎 TON",
}
async def create_crypto_invoice(
    asset: str,
    amount_usd: float,
    description: str,
    payload: str = "",
) -> dict | None:
    """
    Create a CryptoBot invoice.
    amount_usd - approximate USD amount (you should convert RUB→USD before calling).
    Returns dict with {invoice_id, pay_url} or None.
    """
    params = {
        "asset": asset,
        "amount": f"{amount_usd:.4f}",
        "description": description,
        "payload": payload,
        "paid_btn_name": "openBot",
        "paid_btn_url": f"https://t.me/EscapeTheMatrixVPNBot",
        "allow_comments": False,
        "allow_anonymous": False,
        "expires_in": 1800,  # 30 minutes
    }
    async with aiohttp.ClientSession() as s:
        resp = await s.post(f"{API}/createInvoice", json=params, headers=HEADERS)
        data = await resp.json()
        if data.get("ok"):
            inv = data["result"]
            return {"invoice_id": str(inv["invoice_id"]), "pay_url": inv["pay_url"]}
        logger.error(f"CryptoBot error: {data}")
    return None


async def check_crypto_invoice(invoice_id: str) -> str | None:
    """Returns status: active / paid / expired"""
    params = {"invoice_ids": invoice_id}
    async with aiohttp.ClientSession() as s:
        resp = await s.get(f"{API}/getInvoices", params=params, headers=HEADERS)
        data = await resp.json()
        if data.get("ok"):
            items = data["result"].get("items", [])
            if items:
                return items[0].get("status")
    return None


async def get_usd_rate() -> float:
    """Get approximate USD/RUB rate via CryptoBot exchange rates."""
    try:
        async with aiohttp.ClientSession() as s:
            resp = await s.get(f"{API}/getExchangeRates", headers=HEADERS)
            data = await resp.json()
            if data.get("ok"):
                for rate in data["result"]:
                    if rate.get("source") == "USDT" and rate.get("target") == "RUB":
                        return float(rate["rate"])
    except Exception as e:
        logger.warning(f"Could not get USD rate: {e}")
    return 90.0   # fallback

async def rub_to_asset(rub: float, asset: str) -> float:
    """Convert RUB amount to crypto asset amount."""
    usd_rate = await get_usd_rate()
    usd = rub / usd_rate
    if asset == "USDT":
        return round(usd, 2)
    # For other assets get rate vs USDT
    try:
        async with aiohttp.ClientSession() as s:
            resp = await s.get(f"{API}/getExchangeRates", headers=HEADERS)
            data = await resp.json()
            if data.get("ok"):
                for rate in data["result"]:
                    if rate.get("source") == asset and rate.get("target") == "USDT":
                        return round(usd / float(rate["rate"]), 6)
                    if rate.get("source") == "USDT" and rate.get("target") == asset:
                        return round(usd * float(rate["rate"]), 6)
    except Exception:
        pass
    return round(usd, 6)