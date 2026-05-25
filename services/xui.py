# 3x-ui panel API wrapper. Docs: https://github.com/MHSanaei/3x-ui
import json
import uuid
import asyncio
import re
import aiohttp
import logging
from datetime import datetime, timedelta, timezone
from config import config

logger = logging.getLogger(__name__)


def _build_base(host: str, port: int, base_path: str) -> str:
    """Собирает полный базовый URL панели с учётом base_path."""
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
        self._csrf_token: str | None = None
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

    async def _extract_csrf_from_page(self) -> str:
        """Извлекает CSRF токен из HTML страницы логина."""
        s = await self.get_session()
        page = await s.get(f"{self.base}/")
        html = await page.text()
        m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
        token = m.group(1) if m else ""
        logger.debug(f"[{self.label}] CSRF from page: {token[:20]}...")
        return token

    async def login(self) -> str | None:
        """Логин с сохранением CSRF токена и кук."""
        async with self._lock:
            s = await self.get_session()
            try:
                # Получаем свежий CSRF токен
                self._csrf_token = await self._extract_csrf_from_page()
                if not self._csrf_token:
                    logger.error(f"[{self.label}] No CSRF token found on login page")
                    return None

                # Логинимся
                resp = await s.post(
                    f"{self.base}/login",
                    json={"username": self.user, "password": self.password},
                    headers={
                        "Content-Type": "application/json",
                        "X-Csrf-Token": self._csrf_token,
                    },
                )
                logger.debug(f"[{self.label}] Login status: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        # Сохраняем куки
                        all_cookies = []
                        for cookie in s.cookie_jar:
                            all_cookies.append(f"{cookie.key}={cookie.value}")
                        self._cookie = "; ".join(all_cookies)
                        
                        # После успешного логина получаем НОВЫЙ CSRF токен 
                        # (сессионный, меняется после логина)
                        self._csrf_token = await self._extract_csrf_from_page()
                        
                        logger.debug(f"[{self.label}] Login OK, CSRF: {self._csrf_token[:20]}...")
                        return self._cookie
                    else:
                        logger.error(f"[{self.label}] Login failed: {data}")
            except Exception as e:
                logger.error(f"[{self.label}] Login exception: {e}")
        return None

    async def _ensure_auth(self) -> bool:
        """Проверяет и обновляет аутентификацию при необходимости."""
        if not self._csrf_token or not self._cookie:
            return bool(await self.login())
        return True

    async def _headers(self) -> dict:
        """Возвращает заголовки с CSRF токеном и куками."""
        await self._ensure_auth()
        return {
            "Cookie": self._cookie or "",
            "Content-Type": "application/json",
            "X-Csrf-Token": self._csrf_token or "",
        }

    async def _refresh_csrf_if_needed(self, response_status: int) -> bool:
        """Обновляет CSRF токен если получили 401/403."""
        if response_status in (401, 403):
            logger.warning(f"[{self.label}] Auth failed (status {response_status}), re-logging in...")
            self._csrf_token = None
            self._cookie = None
            return bool(await self.login())
        return True

    async def post(self, path: str, payload: dict) -> dict | None:
        """POST запрос с авто-перелогином при 401/403."""
        s = await self.get_session()
        for attempt in range(2):
            headers = await self._headers()
            try:
                resp = await s.post(f"{self.base}{path}", json=payload, headers=headers)
                
                # Проверяем не истекла ли сессия
                if not await self._refresh_csrf_if_needed(resp.status):
                    continue
                
                raw = await resp.text()
                logger.debug(f"[{self.label}] POST {path} -> {resp.status}: {raw[:200]}")
                
                if resp.status == 404:
                    logger.error(f"[{self.label}] 404 Not Found for {path} — check API endpoint")
                    return None
                
                if not raw.strip():
                    logger.error(f"[{self.label}] Empty response for {path}")
                    return None
                    
                return json.loads(raw)
            except Exception as e:
                logger.error(f"[{self.label}] POST exception for {path}: {e}")
                return None
        return None

    async def get(self, path: str) -> dict | None:
        """GET запрос с авто-перелогином."""
        s = await self.get_session()
        for attempt in range(2):
            headers = await self._headers()
            try:
                resp = await s.get(f"{self.base}{path}", headers=headers)
                
                if not await self._refresh_csrf_if_needed(resp.status):
                    continue
                
                raw = await resp.text()
                if not raw.strip():
                    logger.error(f"[{self.label}] Empty GET response for {path}")
                    return None
                    
                return json.loads(raw)
            except Exception as e:
                logger.error(f"[{self.label}] GET exception for {path}: {e}")
                return None
        return None


# ==================== Инициализация ====================
_base = _build_base(config.PANEL_HOST, config.PANEL_PORT, config.PANEL_BASE_PATH)
_panel = _PanelSession(_base, config.PANEL_USER, config.PANEL_PASS, "MASTER")

_panels: dict[str, _PanelSession] = {
    "fi": _panel,
    "nl": _panel,
}

# Inbound IDs из API
REGION_INBOUNDS: dict[str, tuple[int, int]] = {
    "fi": (1, 2),   # (desktop=443, mobile=8443)
    "nl": (3, 4),   # (desktop=443, mobile=8443)
}

# Формат email для разных inbound'ов
# FI: base_email и base_email+"m"
# NL: base_email+"_3" и base_email+"_4"
EMAIL_SUFFIXES = {
    "fi": {"desktop": "", "mobile": "m"},
    "nl": {"desktop": "_3", "mobile": "_4"},
}

REGION_LABELS = {
    "fi": "🇫🇮 Finland",
    "nl": "🇳🇱 Netherlands",
}


def _client_email(base_email: str, inbound_type: str, region: str) -> str:
    """Формирует email клиента с правильным суффиксом."""
    suffix = EMAIL_SUFFIXES.get(region, {}).get(inbound_type, "")
    return f"{base_email}{suffix}"


def _sub_link(email: str) -> str:
    """Ссылка на подписку."""
    return f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"


async def close_session() -> None:
    """Закрыть сессию."""
    await _panel.close()


async def login() -> str | None:
    """Принудительный логин."""
    return await _panel.login()


# ==================== API методы ====================

async def create_client(email: str, days: int, devices_limit: int = 4,
                        region: str = "fi") -> dict | None:
    """Создаёт клиента в desktop и mobile inbound'ах."""
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
                    "subId": email,  # subId = базовый email без суффикса
                    "flow": "xtls-rprx-vision",
                }]
            }),
        }

    url = "/panel/api/inbounds/addClient"

    # Desktop
    desktop_email = _client_email(email, "desktop", region)
    data1 = await panel.post(url, _client_payload(desktop_inbound, desktop_email))
    if not data1 or not data1.get("success"):
        logger.error(f"[{region.upper()}] addClient desktop failed for {desktop_email}: {data1}")
        return None
    logger.info(f"[{region.upper()}] Desktop client created: {desktop_email}")

    # Mobile
    mobile_email = _client_email(email, "mobile", region)
    data2 = await panel.post(url, _client_payload(mobile_inbound, mobile_email))
    if not data2 or not data2.get("success"):
        logger.warning(f"[{region.upper()}] addClient mobile failed for {mobile_email} (desktop OK)")
    else:
        logger.info(f"[{region.upper()}] Mobile client created: {mobile_email}")

    sub_link = _sub_link(email)
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(client_id: str, email: str,
                                extra_days: int, current_expire_ms: int,
                                region: str = "fi",
                                devices_limit: int = 4) -> bool:
    """Продлевает клиента на extra_days дней."""
    new_expire = current_expire_ms + extra_days * 86_400_000
    inbounds = REGION_INBOUNDS.get(region)
    panel = _panels.get(region, _panel)
    if not inbounds:
        return False

    desktop_inbound, mobile_inbound = inbounds
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    results = []

    # Обновляем оба inbound'а
    for inbound_type, inbound_id in [("desktop", desktop_inbound), ("mobile", mobile_inbound)]:
        email_val = _client_email(email, inbound_type, region)
        payload = {
            "id": inbound_id,
            "settings": json.dumps({"clients": [{
                "id": client_id,
                "email": email_val,
                "expiryTime": new_expire,
                "enable": True,
                "flow": "xtls-rprx-vision",
                "subId": email,
                "totalGB": 0,
                "tgId": "",
                "limitIp": devices_limit,
            }]}),
        }
        data = await panel.post(url, payload)
        success = bool(data and data.get("success"))
        results.append(success)
        logger.debug(f"[{region.upper()}] Update expiry for {email_val}: {'OK' if success else 'FAILED'}")

    return any(results)


async def update_client_ip_limit(client_id: str, email: str, limit: int,
                                  region: str = "fi") -> bool:
    """Обновляет лимит IP-адресов."""
    inbounds = REGION_INBOUNDS.get(region)
    panel = _panels.get(region, _panel)
    if not inbounds:
        return False

    desktop_inbound, mobile_inbound = inbounds
    url = f"/panel/api/inbounds/updateClient/{client_id}"
    results = []

    for inbound_type, inbound_id in [("desktop", desktop_inbound), ("mobile", mobile_inbound)]:
        email_val = _client_email(email, inbound_type, region)
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
    """Получает трафик клиента по базовому email."""
    panel = _panels.get(region, _panel)
    # API возвращает трафик по subId (базовый email без суффикса)
    path = f"/panel/api/inbounds/getClientTraffics/{email}"
    data = await panel.get(path)
    if data and data.get("success"):
        return data.get("obj")
    return None


async def get_online_count(email: str, region: str = "fi") -> int | None:
    """Получает количество онлайн-сессий."""
    panel = _panels.get(region, _panel)
    # Используем desktop email для проверки онлайн
    desktop_email = _client_email(email, "desktop", region)
    path = f"/panel/api/inbounds/clientIps/{desktop_email}"
    
    s = await panel.get_session()
    headers = await panel._headers()
    try:
        resp = await s.post(f"{panel.base}{path}", headers=headers)
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
        logger.warning(f"get_online_count failed for {desktop_email} [{region}]: {e}")
    return None