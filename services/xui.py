# 3x-ui panel API wrapper. Docs: https://github.com/MHSanaei/3x-ui
import json
import uuid
import asyncio
import re
import aiohttp
import socket
import logging
from datetime import datetime, timedelta, timezone
from config import config


logger = logging.getLogger(__name__)


def _build_base(host: str, port: int, base_path: str) -> str:
    path = f"/{base_path}" if base_path else ""
    return f"{host}:{port}{path}"


class _PanelSession:
    """Сессия к одной 3x-ui панели с CSRF-аутентификацией."""

    def __init__(self, base: str, user: str, password: str, label: str):
        self.base = base
        self.user = user
        self.password = password
        self.label = label
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._cookie: str | None = None
        self._csrf_token: str | None = None  # DEBUG: сохраняем CSRF токен

    def _new_session(self) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)   # Увеличенный таймаут
        connector = aiohttp.TCPConnector(
            family=socket.AF_INET,          # ТОЛЬКО IPv4 (избегаем IPv6-зависаний)
            ssl=False,                      # Отключаем проверку SSL
            force_close=True                # Принудительно закрываем соединения после запроса
        )
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False                 # Игнорируем HTTP_PROXY/HTTPS_PROXY из окружения
        )

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
                login_page_url = f"{self.base}/"
                logger.info(f"[{self.label}] GET login page: {login_page_url}")  # DEBUG
                
                page = await s.get(login_page_url)
                html = await page.text()
                
                logger.info(f"[{self.label}] Login page status: {page.status}")  # DEBUG
                logger.info(f"[{self.label}] Login page HTML length: {len(html)} chars")  # DEBUG
                
                m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
                csrf_token = m.group(1) if m else ""
                self._csrf_token = csrf_token  # DEBUG: сохраняем
                
                logger.info(f"[{self.label}] CSRF token: '{csrf_token[:30]}...' (len={len(csrf_token)})")  # DEBUG

                login_url = f"{self.base}/login"
                login_payload = {"username": self.user, "password": self.password}
                
                logger.info(f"[{self.label}] POST login: {login_url}")  # DEBUG
                logger.info(f"[{self.label}] Login payload: {json.dumps(login_payload)}")  # DEBUG

                resp = await s.post(
                    login_url,
                    json=login_payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Csrf-Token": csrf_token,
                    },
                )
                
                logger.info(f"[{self.label}] Login response status: {resp.status}")  # DEBUG
                
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"[{self.label}] Login response body: {json.dumps(data)[:200]}")  # DEBUG
                    
                    if data.get("success"):
                        all_cookies = [f"{c.key}={c.value}" for c in s.cookie_jar]
                        self._cookie = "; ".join(all_cookies)
                        logger.info(f"[{self.label}] Login OK. Cookies: {self._cookie[:100]}...")  # DEBUG
                        logger.info(f"[{self.label}] Total cookies: {len(all_cookies)}")  # DEBUG
                        return self._cookie
                    else:
                        logger.error(f"[{self.label}] Login response not success: {data}")  # DEBUG
                else:
                    raw = await resp.text()
                    logger.error(f"[{self.label}] Login failed, status={resp.status}, body={raw[:200]}")  # DEBUG
            except Exception as e:
                logger.error(f"[{self.label}] xui login exception: {e}", exc_info=True)  # DEBUG
        logger.error(f"[{self.label}] xui login failed")
        return None

    async def _headers(self) -> dict:
        if not self._cookie:
            logger.info(f"[{self.label}] No cookies, attempting login...")  # DEBUG
            await self.login()
        
        headers = {
            "Cookie": self._cookie or "",
            "Content-Type": "application/json",
        }
        
        # DEBUG: добавляем CSRF токен в заголовки если он есть
        if self._csrf_token:
            headers["X-Csrf-Token"] = self._csrf_token
        
        logger.info(f"[{self.label}] Headers: Cookie={self._cookie[:50] if self._cookie else 'EMPTY'}..., X-Csrf-Token={self._csrf_token[:20] if self._csrf_token else 'EMPTY'}...")  # DEBUG
        
        return headers

    async def post(self, path: str, payload: dict) -> dict | None:
        """POST с авто-реauth при 401/403."""
        s = await self.get_session()
        for attempt in range(2):
            headers = await self._headers()
            
            full_url = f"{self.base}{path}"
            logger.info(f"[{self.label}] >>> POST attempt={attempt+1}: {full_url}")  # DEBUG
            logger.info(f"[{self.label}] >>> Payload: {json.dumps(payload)[:500]}")  # DEBUG
            
            try:
                resp = await s.post(full_url, json=payload, headers=headers)
                raw = await resp.text()
                
                logger.info(f"[{self.label}] <<< Status: {resp.status}")  # DEBUG
                logger.info(f"[{self.label}] <<< Response: {raw[:500]}")  # DEBUG
                
                if resp.status in (401, 403):
                    logger.warning(f"[{self.label}] session expired (status={resp.status}), re-logging in...")
                    self._cookie = None
                    self._csrf_token = None  # DEBUG
                    await self.login()
                    continue
                    
                if resp.status == 404:
                    logger.error(f"[{self.label}] 404 Not Found for {path}")  # DEBUG
                    logger.error(f"[{self.label}] Full URL: {full_url}")  # DEBUG
                    return None
                    
                if not raw.strip():
                    logger.error(f"[{self.label}] empty response, status={resp.status}")
                    return None
                    
                return json.loads(raw)
            except Exception as e:
                logger.error(f"[{self.label}] POST exception: {e}", exc_info=True)  # DEBUG
                return None
        return None


_base = _build_base(config.PANEL_HOST, config.PANEL_PORT, config.PANEL_BASE_PATH)
logger.info(f"Panel base URL: {_base}")  # DEBUG

_panel = _PanelSession(_base, config.PANEL_USER, config.PANEL_PASS, "MASTER")

_panels: dict[str, _PanelSession] = {
    "fi": _panel,
    "nl": _panel,
}

REGION_INBOUNDS: dict[str, tuple[int, int]] = {
    "fi": (config.INBOUND_FI_DESKTOP, config.INBOUND_FI_MOBILE),
    "nl": (config.INBOUND_NL_DESKTOP, config.INBOUND_NL_MOBILE),
}

logger.info(f"Inbound IDs - FI: {REGION_INBOUNDS['fi']}, NL: {REGION_INBOUNDS['nl']}")  # DEBUG

REGION_LABELS = {
    "fi": "🇫🇮 Finland",
    "nl": "🇳🇱 Netherlands",
}


def _sub_link(email: str) -> str:
    """Ссылка на подписку — одна для всех регионов (единая панель)."""
    return f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"


async def close_session() -> None:
    """Закрыть сессию (вызывается при shutdown бота)."""
    await _panel.close()


async def create_client(email: str, days: int, devices_limit: int = 4,
                        region: str = "fi") -> dict | None:
    logger.info(f"create_client called: email={email}, days={days}, limit={devices_limit}, region={region}")  # DEBUG
    
    inbounds = REGION_INBOUNDS.get(region)
    panel = _panels.get(region)
    if not inbounds or not panel:
        logger.error(f"Unknown region: {region}")
        return None

    desktop_inbound, mobile_inbound = inbounds
    client_id = str(uuid.uuid4())
    expire_ts = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)
    label = REGION_LABELS.get(region, region)
    
    logger.info(f"Client ID: {client_id}, Expire TS: {expire_ts}, Label: {label}")  # DEBUG

    def _client_payload(inbound_id: int, email_val: str) -> dict:
        payload = {
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
        logger.info(f"Payload for inbound {inbound_id}, email {email_val}: {json.dumps(payload)[:500]}")  # DEBUG
        return payload

    url = "/panel/api/inbounds/addClient"
    logger.info(f"Using API URL: {url}")  # DEBUG

    # Desktop
    logger.info(f"[{region.upper()}] Adding desktop client to inbound {desktop_inbound}")  # DEBUG
    data1 = await panel.post(url, _client_payload(desktop_inbound, email))
    if not data1 or not data1.get("success"):
        logger.error(f"[{region.upper()}] xui addClient desktop failed: {data1}")
        return None
    
    logger.info(f"[{region.upper()}] Desktop client added successfully")  # DEBUG

    # Mobile
    logger.info(f"[{region.upper()}] Adding mobile client to inbound {mobile_inbound}")  # DEBUG
    data2 = await panel.post(url, _client_payload(mobile_inbound, f"{email}m"))
    if not data2 or not data2.get("success"):
        logger.warning(f"[{region.upper()}] xui addClient mobile failed: {data2} (desktop OK)")
    else:
        logger.info(f"[{region.upper()}] Mobile client added successfully")  # DEBUG

    sub_link = _sub_link(email)
    logger.info(f"Subscription link: {sub_link}")  # DEBUG
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(client_id: str, email: str,
                                extra_days: int, current_expire_ms: int,
                                region: str = "fi",
                                devices_limit: int = 4) -> bool:
    logger.info(f"update_client_expiry: client_id={client_id}, email={email}, extra_days={extra_days}, region={region}")  # DEBUG
    
    new_expire = current_expire_ms + extra_days * 86_400_000
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_FI_DESKTOP, config.INBOUND_FI_MOBILE))
    panel = _panels.get(region, _panel)
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    results = []

    desktop_inbound, mobile_inbound = inbounds

    logger.info(f"Updating desktop inbound {desktop_inbound}, new expire: {new_expire}")  # DEBUG
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
    logger.info(f"Desktop update result: {results[-1]}")  # DEBUG

    logger.info(f"Updating mobile inbound {mobile_inbound}")  # DEBUG
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
    logger.info(f"Mobile update result: {results[-1]}")  # DEBUG

    return any(results)


async def update_client_ip_limit(client_id: str, email: str, limit: int,
                                  region: str = "fi") -> bool:
    logger.info(f"update_client_ip_limit: client_id={client_id}, email={email}, limit={limit}, region={region}")  # DEBUG
    
    inbounds = REGION_INBOUNDS.get(region, (config.INBOUND_FI_DESKTOP, config.INBOUND_FI_MOBILE))
    panel = _panels.get(region, _panel)
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    desktop_inbound, mobile_inbound = inbounds
    results = []

    for inbound_id, email_val in [
        (desktop_inbound, email),
        (mobile_inbound, f"{email}m"),
    ]:
        logger.info(f"Updating IP limit for inbound {inbound_id}, email {email_val}")  # DEBUG
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
    logger.info(f"get_client_traffic: email={email}, region={region}")  # DEBUG
    
    panel = _panels.get(region, _panel)
    s = await panel.get_session()
    headers = await panel._headers()
    
    url = f"{panel.base}/panel/api/inbounds/getClientTraffics/{email}"
    logger.info(f"GET traffic URL: {url}")  # DEBUG
    
    try:
        resp = await s.get(url, headers=headers)
        logger.info(f"Traffic response status: {resp.status}")  # DEBUG
        
        data = await resp.json()
        if data.get("success"):
            logger.info(f"Traffic data: {json.dumps(data.get('obj'))[:200]}")  # DEBUG
            return data.get("obj")
        else:
            logger.warning(f"Traffic request not success: {data}")  # DEBUG
    except Exception as e:
        logger.warning(f"get_client_traffic error [{region}]: {e}", exc_info=True)  # DEBUG
    return None


async def get_online_count(email: str, region: str = "fi") -> int | None:
    logger.info(f"get_online_count: email={email}, region={region}")  # DEBUG
    
    panel = _panels.get(region, _panel)
    s = await panel.get_session()
    headers = await panel._headers()
    
    url = f"{panel.base}/panel/api/inbounds/clientIps/{email}"
    logger.info(f"Online count URL: {url}")  # DEBUG
    
    try:
        resp = await s.post(url, headers=headers)
        logger.info(f"Online count response status: {resp.status}")  # DEBUG
        
        if resp.status != 200:
            return None
        data = await resp.json()
        if data.get("success"):
            obj = data.get("obj")
            if not obj:
                return 0
            if isinstance(obj, list):
                count = len(obj)
                logger.info(f"Online count (list): {count}")  # DEBUG
                return count
            ips = [ip.strip() for ip in str(obj).split("\n") if ip.strip()]
            count = len(ips)
            logger.info(f"Online count (text): {count}")  # DEBUG
            return count
    except Exception as e:
        logger.warning(f"get_online_count failed for {email} [{region}]: {e}", exc_info=True)  # DEBUG
    return None


async def login():
    return await _panel.login()