#3x-ui panel API wrapper. Docs: https://github.com/MHSanaei/3x-ui
import json
import uuid
import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger(__name__)

_path = f"/{config.PANEL_BASE_PATH}" if config.PANEL_BASE_PATH else ""
BASE = f"{config.PANEL_BASE_URL}{_path}"

# Единая сессия на весь lifecycle + lock для защиты логина
_session: aiohttp.ClientSession | None = None
_session_lock = asyncio.Lock()
SESSION_COOKIE: str | None = None


def _new_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = _new_session()
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


async def login() -> str | None:
    """Логин в панель. Защищён lock от race condition."""
    global SESSION_COOKIE
    async with _session_lock:
        s = await get_session()
        try:
            resp = await s.post(
                f"{BASE}/login",
                json={"username": config.PANEL_USER, "password": config.PANEL_PASS},
            )
            logger.debug(f"xui login status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    set_cookie = resp.headers.get("Set-Cookie", "")
                    if set_cookie:
                        SESSION_COOKIE = set_cookie.split(";")[0].strip()
                    else:
                        all_cookies = [f"{c.key}={c.value}" for c in s.cookie_jar]
                        SESSION_COOKIE = "; ".join(all_cookies)
                    logger.debug(f"xui login OK, cookie='{SESSION_COOKIE}'")
                    return SESSION_COOKIE
        except Exception as e:
            logger.error(f"xui login exception: {e}")
    logger.error("xui login failed")
    return None


async def _headers(retry: bool = True) -> dict:
    """Возвращает заголовки. При отсутствии cookie — логинится."""
    global SESSION_COOKIE
    if not SESSION_COOKIE:
        await login()
    return {"Cookie": SESSION_COOKIE or "", "Content-Type": "application/json"}


async def _post_with_reauth(url: str, payload: dict) -> dict | None:
    """POST с автоматическим re-login если сессия истекла (401/403)."""
    global SESSION_COOKIE
    s = await get_session()
    for attempt in range(2):
        headers = await _headers()
        try:
            resp = await s.post(url, json=payload, headers=headers)
            if resp.status in (401, 403):
                logger.warning("xui session expired, re-logging in...")
                SESSION_COOKIE = None
                await login()
                continue
            raw = await resp.text()
            logger.debug(f"xui POST {url} status={resp.status} raw='{raw[:200]}'")
            if not raw.strip():
                logger.error(f"xui empty response, status={resp.status}")
                return None
            return json.loads(raw)
        except Exception as e:
            logger.error(f"xui POST exception: {e}")
            return None
    return None


# --- Маппинг регион → inbound IDs ---
# Формат: region_code -> (desktop_inbound_id, mobile_inbound_id)
REGION_INBOUNDS: dict[str, tuple[int, int]] = {
    "fi": (config.INBOUND_ID, config.INBOUND_MOBILE_ID),
    # "nl": (config.INBOUND_NL_ID, config.INBOUND_NL_MOBILE_ID),  # раскомментировать при добавлении NL
}

REGION_LABELS = {
    "fi": "🇫🇮 Finland",
    "nl": "🇳🇱 Netherlands",
}


async def create_client(email: str, days: int, devices_limit: int = 3,
                        region: str = "fi") -> dict | None:
    inbounds = REGION_INBOUNDS.get(region)
    if not inbounds:
        logger.error(f"Unknown region: {region}")
        return None

    desktop_inbound, mobile_inbound = inbounds
    client_id = str(uuid.uuid4())
    expire_ts = int((datetime.utcnow() + timedelta(days=days)).timestamp() * 1000)
    label = REGION_LABELS.get(region, region)

    def _client_payload(inbound_id: int, email_val: str) -> dict:
        return {
            "id": inbound_id,
            "settings": json.dumps({
                "clients": [{
                    "id": client_id,
                    "email": email_val,
                    "remark": label,
                    "limitIp": devices_limit,
                    "totalGB": 0,
                    "expiryTime": expire_ts,
                    "enable": True,
                    "tgId": "",
                    "subId": email,
                    "flow": "xtls-rprx-vision",
                }]
            }),
        }

    url = f"{BASE}/panel/api/inbounds/addClient"

    # Desktop
    data1 = await _post_with_reauth(url, _client_payload(desktop_inbound, email))
    if not data1 or not data1.get("success"):
        logger.error(f"xui addClient desktop failed: {data1}")
        return None

    # Mobile
    data2 = await _post_with_reauth(url, _client_payload(mobile_inbound, f"{email}m"))
    if not data2 or not data2.get("success"):
        logger.warning(f"xui addClient mobile failed: {data2} (desktop OK)")

    sub_link = f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(client_id: str, email: str,
                                extra_days: int, current_expire_ms: int,
                                region: str = "fi") -> bool:
    new_expire = current_expire_ms + extra_days * 86_400_000
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_ID, config.INBOUND_MOBILE_ID))
    url = f"{BASE}/panel/api/inbounds/updateClient/{client_id}"
    results = []
    for inbound_id in inbounds:
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [{
                "id": client_id, "email": email,
                "expiryTime": new_expire, "enable": True, "flow": "xtls-rprx-vision",
            }]}),
        }
        data = await _post_with_reauth(url, payload)
        results.append(bool(data and data.get("success")))
    return any(results)


async def update_client_ip_limit(client_id: str, email: str, limit: int,
                                  region: str = "fi") -> bool:
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_ID, config.INBOUND_MOBILE_ID))
    url = f"{BASE}/panel/api/inbounds/updateClient/{client_id}"
    results = []
    for inbound_id in inbounds:
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [{
                "id": client_id, "email": email,
                "limitIp": limit, "enable": True, "flow": "xtls-rprx-vision",
            }]}),
        }
        data = await _post_with_reauth(url, payload)
        results.append(bool(data and data.get("success")))
    return any(results)


async def get_client_traffic(email: str) -> dict | None:
    s = await get_session()
    headers = await _headers()
    try:
        resp = await s.get(
            f"{BASE}/panel/api/inbounds/getClientTraffics/{email}",
            headers=headers,
        )
        data = await resp.json()
        if data.get("success"):
            return data.get("obj")
    except Exception as e:
        logger.warning(f"get_client_traffic error: {e}")
    return None


async def get_online_count(email: str) -> int | None:
    s = await get_session()
    headers = await _headers()
    try:
        resp = await s.post(
            f"{BASE}/panel/api/inbounds/clientIps/{email}",
            headers=headers,
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