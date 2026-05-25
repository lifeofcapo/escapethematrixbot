# 3x-ui panel API wrapper v3.1.0
# Docs: https://documenter.getpostman.com/view/5146551/2sBXwmPC6M
import json
import uuid
import asyncio
import socket
import logging
import aiohttp
from datetime import datetime, timedelta, timezone
from config import config

logger = logging.getLogger(__name__)


def _build_base(host: str, port: int, base_path: str) -> str:
    path = f"/{base_path}" if base_path else ""
    return f"{host}:{port}{path}"


class _PanelSession:
    """Сессия к одной 3x-ui панели с Bearer Token авторизацией."""

    def __init__(self, base: str, token: str, label: str):
        self.base = base
        self.token = token
        self.label = label
        self._session: aiohttp.ClientSession | None = None

    def _new_session(self) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=60, connect=30)
        connector = aiohttp.TCPConnector(
            family=socket.AF_INET,
            ssl=False,
            force_close=True,
        )
        return aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        )

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = self._new_session()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    async def post(self, path: str, payload: dict | None = None) -> dict | None:
        s = await self.get_session()
        full_url = f"{self.base}{path}"
        logger.info(f"[{self.label}] POST {full_url}")
        if payload:
            logger.info(f"[{self.label}] Payload: {json.dumps(payload)[:500]}")
        try:
            resp = await s.post(
                full_url,
                json=payload,
                headers=self._headers(),
            )
            raw = await resp.text()
            logger.info(f"[{self.label}] Status: {resp.status} | Response: {raw[:500]}")
            if not raw.strip():
                logger.error(f"[{self.label}] Empty response, status={resp.status}")
                return None
            return json.loads(raw)
        except Exception as e:
            logger.error(f"[{self.label}] POST exception: {e}", exc_info=True)
            return None

    async def get(self, path: str) -> dict | None:
        s = await self.get_session()
        full_url = f"{self.base}{path}"
        logger.info(f"[{self.label}] GET {full_url}")
        try:
            resp = await s.get(full_url, headers=self._headers())
            raw = await resp.text()
            logger.info(f"[{self.label}] Status: {resp.status} | Response: {raw[:500]}")
            if not raw.strip():
                logger.error(f"[{self.label}] Empty response, status={resp.status}")
                return None
            return json.loads(raw)
        except Exception as e:
            logger.error(f"[{self.label}] GET exception: {e}", exc_info=True)
            return None


# --- инициализация ---

_base = _build_base(config.PANEL_HOST, config.PANEL_PORT, config.PANEL_BASE_PATH)
logger.info(f"Panel base URL: {_base}")

_panel = _PanelSession(_base, config.PANEL_API_TOKEN, "MASTER")

_panels: dict[str, _PanelSession] = {
    "fi": _panel,
    "nl": _panel,
}

REGION_INBOUNDS: dict[str, tuple[int, int]] = {
    "fi": (config.INBOUND_FI_DESKTOP, config.INBOUND_FI_MOBILE),
    "nl": (config.INBOUND_NL_DESKTOP, config.INBOUND_NL_MOBILE),
}

REGION_LABELS = {
    "fi": "🇫🇮 Finland",
    "nl": "🇳🇱 Netherlands",
}

logger.info(f"Inbound IDs - FI: {REGION_INBOUNDS['fi']}, NL: {REGION_INBOUNDS['nl']}")


def _sub_link(email: str) -> str:
    """Ссылка на подписку — одна для всех регионов (единая панель)."""
    return f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"


async def close_session() -> None:
    """Закрыть сессию (вызывается при shutdown бота)."""
    await _panel.close()


async def create_client(
    email: str,
    days: int,
    devices_limit: int = 4,
    region: str = "fi",
) -> dict | None:
    """
    Создаёт клиента и привязывает его к desktop + mobile inbound одним запросом.
    Возвращает {"client_id": ..., "email": ..., "sub_link": ...} или None при ошибке.
    """
    logger.info(f"create_client: email={email}, days={days}, limit={devices_limit}, region={region}")

    panel = _panels.get(region, _panel)

    # Собираем все инбаунды со всех регионов
    all_inbound_ids = []
    for r_inbounds in REGION_INBOUNDS.values():
        all_inbound_ids.extend(list(r_inbounds))
    client_id = str(uuid.uuid4())
    expire_ts = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)

    logger.info(f"client_id={client_id}, expire_ts={expire_ts}, inbounds={all_inbound_ids}")

    payload = {
        "client": {
            "id": client_id,
            "email": email,
            "limitIp": devices_limit,
            "totalGB": 0,
            "expiryTime": expire_ts,
            "enable": True,
            "tgId": 0,
            "subId": email,
            "flow": "xtls-rprx-vision",
        },
        "inboundIds": all_inbound_ids,
    }

    data = await panel.post("/panel/api/clients/add", payload)
    if not data or not data.get("success"):
        logger.error(f"[{region.upper()}] addClient failed: {data}")
        return None

    logger.info(f"[{region.upper()}] Client created successfully: {email}")
    sub_link = _sub_link(email)
    logger.info(f"Subscription link: {sub_link}")
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def update_client_expiry(
    client_id: str,
    email: str,
    extra_days: int,
    current_expire_ms: int,
    region: str = "fi",
    devices_limit: int = 4,
) -> bool:
    """Продлевает срок действия клиента."""
    logger.info(f"update_client_expiry: email={email}, extra_days={extra_days}, region={region}")

    new_expire = current_expire_ms + extra_days * 86_400_000
    panel = _panels.get(region, _panel)

    payload = {
        "email": email,
        "totalGB": 0,
        "expiryTime": new_expire,
        "enable": True,
        "tgId": 0,
        "limitIp": devices_limit,
        "flow": "xtls-rprx-vision",
        "subId": email,
    }

    data = await panel.post(f"/panel/api/clients/update/{email}", payload)
    result = bool(data and data.get("success"))
    logger.info(f"update_client_expiry result: {result}, new_expire={new_expire}")
    return result


async def update_client_ip_limit(
    client_id: str,
    email: str,
    limit: int,
    region: str = "fi",
) -> bool:
    """Обновляет лимит IP-адресов клиента."""
    logger.info(f"update_client_ip_limit: email={email}, limit={limit}, region={region}")

    panel = _panels.get(region, _panel)

    payload = {
        "email": email,
        "totalGB": 0,
        "expiryTime": 0,  # 0 = не менять (панель игнорирует при update)
        "enable": True,
        "tgId": 0,
        "limitIp": limit,
        "flow": "xtls-rprx-vision",
        "subId": email,
    }

    data = await panel.post(f"/panel/api/clients/update/{email}", payload)
    result = bool(data and data.get("success"))
    logger.info(f"update_client_ip_limit result: {result}")
    return result


async def get_client_traffic(email: str, region: str = "fi") -> dict | None:
    """
    Возвращает трафик клиента:
    {"email": ..., "up": ..., "down": ..., "total": ..., "expiryTime": ...}
    """
    logger.info(f"get_client_traffic: email={email}, region={region}")

    panel = _panels.get(region, _panel)
    data = await panel.get(f"/panel/api/clients/traffic/{email}")

    if data and data.get("success"):
        logger.info(f"Traffic data: {json.dumps(data.get('obj'))[:200]}")
        return data.get("obj")

    logger.warning(f"get_client_traffic failed [{region}]: {data}")
    return None


async def get_online_count(email: str, region: str = "fi") -> int | None:
    """Возвращает количество активных IP-соединений клиента."""
    logger.info(f"get_online_count: email={email}, region={region}")

    panel = _panels.get(region, _panel)
    data = await panel.post(f"/panel/api/clients/ips/{email}")

    if not data or not data.get("success"):
        logger.warning(f"get_online_count failed [{region}]: {data}")
        return None

    obj = data.get("obj")
    if not obj:
        return 0
    if isinstance(obj, list):
        return len(obj)
    # формат "ip (timestamp)\nip (timestamp)\n..."
    ips = [line.strip() for line in str(obj).split("\n") if line.strip()]
    return len(ips)


async def login():
    """Заглушка для обратной совместимости — Bearer Token не требует логина."""
    logger.info("login() called — no-op with Bearer Token auth")
    return True