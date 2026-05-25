# 3x-ui panel API wrapper. Docs: https://github.com/MHSanaei/3x-ui
import json
import uuid
import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta, timezone
from config import config

logger = logging.getLogger(__name__)

def _build_base(host: str, port: int, base_path: str) -> str:
    path = f"/{base_path}" if base_path else ""
    return f"{host}:{port}{path}"


class _PanelSession:
    """Одиночная сессия к одной 3x-ui панели."""

    def __init__(self, base: str, user: str, password: str, label: str):
        self.base = base
        self.user = user
        self.password = password
        self.label = label          # для логов: "FI" / "NL"
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._cookie: str | None = None

    def _new_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._new_session()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def login(self) -> str | None:
        async with self._lock:
            s = await self.get_session()
            try:
                # 3x-ui v3+: CSRF токен берётся из мета-тега HTML страницы логина
                import re
                page = await s.get(f"{self.base}/")
                html = await page.text()
                m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
                csrf_token = m.group(1) if m else ""
                logger.debug(f"[{self.label}] csrf token obtained")

                resp = await s.post(
                    f"{self.base}/login",
                    json={"username": self.user, "password": self.password},
                    headers={
                        "Content-Type": "application/json",
                        "X-Csrf-Token": csrf_token,
                    },
                )
                logger.debug(f"[{self.label}] xui login status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        all_cookies = [f"{c.key}={c.value}" for c in s.cookie_jar]
                        self._cookie = "; ".join(all_cookies)
                        logger.debug(f"[{self.label}] xui login OK")
                        return self._cookie
            except Exception as e:
                logger.error(f"[{self.label}] xui login exception: {e}")
        logger.error(f"[{self.label}] xui login failed")
        return None

    async def _headers(self) -> dict:
        if not self._cookie:
            await self.login()
        return {"Cookie": self._cookie or "", "Content-Type": "application/json"}

    async def post(self, path: str, payload: dict) -> dict | None:
        """POST с авто-реauth при 401/403."""
        s = await self.get_session()
        for attempt in range(2):
            headers = await self._headers()
            try:
                resp = await s.post(f"{self.base}{path}", json=payload, headers=headers)
                if resp.status in (401, 403):
                    logger.warning(f"[{self.label}] session expired, re-logging in...")
                    self._cookie = None
                    await self.login()
                    continue
                raw = await resp.text()
                logger.debug(f"[{self.label}] POST {path} status={resp.status} raw='{raw[:200]}'")
                if not raw.strip():
                    logger.error(f"[{self.label}] empty response, status={resp.status}")
                    return None
                return json.loads(raw)
            except Exception as e:
                logger.error(f"[{self.label}] POST exception: {e}")
                return None
        return None

_fi_base = _build_base(config.PANEL_HOST, config.PANEL_PORT, config.PANEL_BASE_PATH)
_nl_base = _build_base(config.PANEL_NL_HOST, config.PANEL_NL_PORT, config.PANEL_NL_BASE_PATH)

_panels: dict[str, _PanelSession] = {
    "fi": _PanelSession(_fi_base, config.PANEL_USER, config.PANEL_PASS, "FI"),
    "nl": _PanelSession(_nl_base, config.PANEL_NL_USER, config.PANEL_NL_PASS, "NL"),
}

REGION_INBOUNDS: dict[str, tuple[int, int]] = {
    "fi": (config.INBOUND_ID, config.INBOUND_MOBILE_ID),
    "nl": (config.INBOUND_NL_ID, config.INBOUND_NL_MOBILE_ID),
}

REGION_LABELS = {
    "fi": "🇫🇮 Finland",
    "nl": "🇳🇱 Netherlands",
}

def _sub_link(email: str, region: str) -> str:
    if region == "nl":
        return f"{config.SUB_NL_HOST}:{config.SUB_NL_PORT}/sub/{email}"
    return f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"


async def close_session() -> None:
    """Закрыть все сессии (вызывается при shutdown бота)."""
    for panel in _panels.values():
        await panel.close()

async def create_client(email: str, days: int, devices_limit: int = 4,
                        region: str = "fi") -> dict | None:
    inbounds = REGION_INBOUNDS.get(region)
    panel = _panels.get(region)
    if not inbounds or not panel:
        logger.error(f"Unknown region: {region}")
        return None

    desktop_inbound, mobile_inbound = inbounds
    client_id = str(uuid.uuid4())
    expire_ts = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)
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

    url = "/panel/api/inbounds/addClient"

    # Desktop
    data1 = await panel.post(url, _client_payload(desktop_inbound, email))
    if not data1 or not data1.get("success"):
        logger.error(f"[{region.upper()}] xui addClient desktop failed: {data1}")
        return None

    # Mobile
    data2 = await panel.post(url, _client_payload(mobile_inbound, f"{email}m"))
    if not data2 or not data2.get("success"):
        logger.warning(f"[{region.upper()}] xui addClient mobile failed: {data2} (desktop OK)")

    sub_link = _sub_link(email, region)
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(client_id: str, email: str,
                                extra_days: int, current_expire_ms: int,
                                region: str = "fi",
                                devices_limit: int = 4) -> bool:
    new_expire = current_expire_ms + extra_days * 86_400_000
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_ID, config.INBOUND_MOBILE_ID))
    panel = _panels.get(region, _panels["fi"])
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    results = []

    desktop_inbound, mobile_inbound = inbounds

    payload_desktop = {
        "id": desktop_inbound,
        "settings": json.dumps({"clients": [{
            "id": client_id,
            "email": email,          
            "expiryTime": new_expire,
            "enable": True,
            "flow": "xtls-rprx-vision",
            "subId": email,
            "totalGB": 0,
            "tgId": "",
            "limitIp": devices_limit,
        }]}),
    }
    data = await panel.post(url, payload_desktop)
    results.append(bool(data and data.get("success")))

    payload_mobile = {
        "id": mobile_inbound,
        "settings": json.dumps({"clients": [{
            "id": client_id,
            "email": f"{email}m",     
            "expiryTime": new_expire,
            "enable": True,
            "flow": "xtls-rprx-vision",
            "subId": email,
            "totalGB": 0,
            "tgId": "",
            "limitIp": devices_limit,
        }]}),
    }
    data = await panel.post(url, payload_mobile)
    results.append(bool(data and data.get("success")))

    return any(results)


async def update_client_ip_limit(client_id: str, email: str, limit: int,
                                  region: str = "fi") -> bool:
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_ID, config.INBOUND_MOBILE_ID))
    panel = _panels.get(region, _panels["fi"])
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    desktop_inbound, mobile_inbound = inbounds
    results = []

    for inbound_id, email_val in [
        (desktop_inbound, email),
        (mobile_inbound, f"{email}m"),
    ]:
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [{
                "id": client_id,
                "email": email_val,
                "limitIp": limit,
                "enable": True,
                "flow": "xtls-rprx-vision",
                "subId": email,
                "totalGB": 0,
                "tgId": "",
            }]}),
        }
        data = await panel.post(url, payload)
        results.append(bool(data and data.get("success")))

    return any(results)

async def get_client_traffic(email: str, region: str = "fi") -> dict | None:
    panel = _panels.get(region, _panels["fi"])
    s = await panel.get_session()
    headers = await panel._headers()
    try:
        resp = await s.get(
            f"{panel.base}/panel/api/inbounds/getClientTraffics/{email}",
            headers=headers,
        )
        data = await resp.json()
        if data.get("success"):
            return data.get("obj")
    except Exception as e:
        logger.warning(f"get_client_traffic error [{region}]: {e}")
    return None


async def get_online_count(email: str, region: str = "fi") -> int | None:
    panel = _panels.get(region, _panels["fi"])
    s = await panel.get_session()
    headers = await panel._headers()
    try:
        resp = await s.post(
            f"{panel.base}/panel/api/inbounds/clientIps/{email}",
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
        logger.warning(f"get_online_count failed for {email} [{region}]: {e}")
    return None


# Обратная совместимость (старые вызовы без session)
async def login():
    return await _panels["fi"].login()