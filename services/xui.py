"""
3x-ui panel API wrapper.
Docs: https://github.com/MHSanaei/3x-ui
"""
import json
import uuid
import aiohttp
import logging
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger(__name__)

_path = f"/{config.PANEL_BASE_PATH}" if config.PANEL_BASE_PATH else ""
BASE = f"{config.PANEL_BASE_URL}{_path}"
SESSION_COOKIE: str | None = None


async def _get_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


async def login() -> str | None:
    global SESSION_COOKIE
    async with await _get_session() as s:
        resp = await s.post(
            f"{BASE}/login",
            json={"username": config.PANEL_USER, "password": config.PANEL_PASS},
        )
        logger.info(f"login status: {resp.status}")

        if resp.status == 200:
            data = await resp.json()
            if data.get("success"):
                set_cookie = resp.headers.get("Set-Cookie", "")
                if set_cookie:
                    SESSION_COOKIE = set_cookie.split(";")[0].strip()
                else:
                    all_cookies = []
                    for cookie in s.cookie_jar:
                        all_cookies.append(f"{cookie.key}={cookie.value}")
                    SESSION_COOKIE = "; ".join(all_cookies)

                logger.info(f"3x-ui login successful, cookie='{SESSION_COOKIE}'")
                return SESSION_COOKIE if SESSION_COOKIE else None

    logger.error("3x-ui login failed")
    return None


async def _headers() -> dict:
    if not SESSION_COOKIE:
        await login()
    return {"Cookie": SESSION_COOKIE or "", "Content-Type": "application/json"}


async def create_client(email: str, days: int, devices_limit: int = 3) -> dict | None:
    client_id = str(uuid.uuid4())
    expire_ts = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)

    def _client_payload(inbound_id: int, remark: str, email_override: str = None) -> dict:
        return {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [
                    {
                        "id": client_id,
                        "email": email_override if email_override else email,
                        "remark": remark,
                        "limitIp": devices_limit,
                        "totalGB": 0,
                        "expiryTime": expire_ts,
                        "enable": True,
                        "tgId": "",
                        "subId": email,  # subId одинаковый — ключ подписки
                        "flow": "xtls-rprx-vision",
                    }
                ]
            }),
        }

    await login()
    if not SESSION_COOKIE:
        logger.error("Login failed — no cookie")
        return None

    headers = {"Cookie": SESSION_COOKIE, "Content-Type": "application/json"}
    url = f"{BASE}/panel/api/inbounds/addClient"

    async with await _get_session() as s:
        # Desktop
        payload1 = _client_payload(config.INBOUND_ID, "🇫🇮 Finland Main")
        logger.info(f"POST {url} inbound={config.INBOUND_ID}")

        resp1 = await s.post(url, json=payload1, headers=headers)
        raw1 = await resp1.text()
        logger.info(f"desktop status={resp1.status} raw='{raw1}'")

        if not raw1.strip():
            logger.error(f"Empty response, status={resp1.status}")
            return None

        try:
            data1 = json.loads(raw1)
        except Exception as e:
            logger.error(f"JSON parse error: {e}, raw='{raw1}'")
            return None

        if not data1.get("success"):
            logger.error(f"addClient desktop failed: {data1}")
            return None

        # Mobile — email с суффиксом чтобы не было дубликата, subId тот же
        payload2 = _client_payload(
            config.INBOUND_MOBILE_ID,
            "🇫🇮 Finland Mobile",
            email_override=f"{email}m",
        )
        resp2 = await s.post(url, json=payload2, headers=headers)
        raw2 = await resp2.text()
        logger.info(f"mobile status={resp2.status} raw='{raw2}'")

        sub_link = f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"
        return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(client_id: str, email: str,
                                extra_days: int, current_expire_ms: int) -> bool:
    new_expire = current_expire_ms + extra_days * 86_400_000
    results = []
    for inbound_id in (config.INBOUND_ID, config.INBOUND_MOBILE_ID):
        payload = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [
                    {
                        "id": client_id,
                        "email": email,
                        "expiryTime": new_expire,
                        "enable": True,
                        "flow": "xtls-rprx-vision",
                    }
                ]
            }),
        }
        async with await _get_session() as s:
            resp = await s.post(
                f"{BASE}/panel/api/inbounds/updateClient/{client_id}",
                json=payload,
                headers=await _headers(),
            )
            data = await resp.json()
            results.append(bool(data.get("success")))
    return any(results)


async def update_client_ip_limit(client_id: str, email: str, limit: int) -> bool:
    results = []
    for inbound_id in (config.INBOUND_ID, config.INBOUND_MOBILE_ID):
        payload = {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [
                    {
                        "id": client_id,
                        "email": email,
                        "limitIp": limit,
                        "enable": True,
                        "flow": "xtls-rprx-vision",
                    }
                ]
            }),
        }
        async with await _get_session() as s:
            resp = await s.post(
                f"{BASE}/panel/api/inbounds/updateClient/{client_id}",
                json=payload,
                headers=await _headers(),
            )
            data = await resp.json()
            results.append(bool(data.get("success")))
    return any(results)


async def get_client_traffic(email: str) -> dict | None:
    """Get traffic stats for a client."""
    async with await _get_session() as s:
        resp = await s.get(
            f"{BASE}/panel/api/inbounds/getClientTraffics/{email}",
            headers=await _headers(),
        )
        data = await resp.json()
        if data.get("success"):
            return data.get("obj")
    return None


async def get_online_count(email: str) -> int | None:
    """
    Get the number of currently connected devices for a client.
    Uses the 3x-ui onlines endpoint.
    Returns count of active IPs, or None if unavailable.
    """
    try:
        async with await _get_session() as s:
            resp = await s.post(
                f"{BASE}/panel/api/inbounds/clientIps/{email}",
                headers=await _headers(),
            )
            if resp.status != 200:
                return None
            data = await resp.json()
            if data.get("success"):
                obj = data.get("obj")
                if not obj:
                    return 0
                if isinstance(obj, list):
                    return len(obj)
                ips = [ip.strip() for ip in str(obj).split("\n") if ip.strip()]
                return len(ips)
    except Exception as e:
        logger.warning(f"get_online_count failed for {email}: {e}")
    return None