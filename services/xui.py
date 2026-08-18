# 3x-ui panel API wrapper
# Compatible with 3x-ui v3.3.0+ (standalone client model)
# Docs: https://docs.sanaei.dev/docs/

import json
import uuid
import logging
import socket
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
            resp = await s.post(full_url, json=payload, headers=self._headers())
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

EXTRA_INBOUNDS: list[int] = [
    config.INBOUND_MOBILE_1,
    config.INBOUND_MOBILE_2,
    config.INBOUND_MOBILE_3,
    config.INBOUND_MOBILE_4,
]

logger.info(
    f"Inbound IDs — FI: {REGION_INBOUNDS['fi']}, "
    f"NL: {REGION_INBOUNDS['nl']}, "
    f"Extra: {EXTRA_INBOUNDS}"
)


def _all_inbound_ids() -> list[int]:
    """Список всех inbound ID для нового клиента."""
    ids: list[int] = []
    for desktop, mobile in REGION_INBOUNDS.values():
        ids.extend([desktop, mobile])
    ids.extend(EXTRA_INBOUNDS)
    return ids


def _sub_link(email: str) -> str:
    """Ссылка на подписку клиента."""
    return f"{config.SUB_HOST}:{config.SUB_PORT}/sub/{email}"


async def close_session() -> None:
    await _panel.close()


async def create_client(
    email: str,
    days: int,
    devices_limit: int = 4,
    region: str = "fi",
) -> dict | None:
    """
    Создание клиента через новый API /panel/api/clients/add.
    
    В v3.3+ клиент — самостоятельная сущность, прикрепляемая к inbound-ам
    через inboundIds. subId приравниваем к email для удобства.

    Возвращает {"client_id": ..., "email": ..., "sub_link": ...} или None.
    """
    logger.info(
        f"create_client: email={email}, days={days}, "
        f"limit={devices_limit}, region={region}"
    )

    panel = _panels.get(region, _panel)
    all_inbound_ids = _all_inbound_ids()
    client_id = str(uuid.uuid4())
    expire_ts = int(
        (datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000
    )

    logger.info(
        f"client_id={client_id}, expire_ts={expire_ts}, "
        f"inbounds={all_inbound_ids}"
    )

    # v3.3+ формат: { "client": {...}, "inboundIds": [...] }
    # Секреты (UUID для VLESS) сервер генерирует сам если не переданы,
    # но мы передаём явно чтобы сохранить в БД.
    payload = {
        "client": {
            "id": client_id,           # UUID для VLESS
            "email": email,
            "subId": email,            # используем email как subId
            "limitIp": devices_limit,
            "totalGB": 0,              # 0 = безлимитный трафик
            "expiryTime": expire_ts,
            "enable": True,
            "tgId": 0,
            "flow": "xtls-rprx-vision",
            "comment": "",
        },
        "inboundIds": all_inbound_ids,
    }

    data = await panel.post("/panel/api/clients/add", payload)
    if not data or not data.get("success"):
        logger.error(f"[{region.upper()}] addClient failed: {data}")
        return None

    logger.info(f"[{region.upper()}] Client created: {email}")
    sub_link = _sub_link(email)
    logger.info(f"Subscription link: {sub_link}")
    return {"client_id": client_id, "email": email, "sub_link": sub_link}


async def get_client(email: str, region: str = "fi") -> dict | None:
    """
    Получить полные данные клиента по email.
    Возвращает {"client": {...}, "inboundIds": [...], "externalLinks": [...]} или None.
    
    Используется перед update чтобы не затирать поля (сервер делает полную замену).
    """
    logger.info(f"get_client: email={email}, region={region}")
    panel = _panels.get(region, _panel)
    data = await panel.get(f"/panel/api/clients/get/{email}")
    if data and data.get("success"):
        obj = data.get("obj")
        logger.info(f"get_client result: {json.dumps(obj)[:300]}")
        return obj
    logger.warning(f"get_client failed [{region}]: {data}")
    return None


async def update_client_expiry(
    client_id: str,
    email: str,
    extra_days: int,
    current_expire_ms: int,
    region: str = "fi",
    devices_limit: int = 4,
) -> bool:
    """
    Продлевает срок действия клиента.

    ВАЖНО: v3.3+ API /clients/update/:email делает полную замену записи,
    поэтому сначала получаем текущие данные и патчим только expiryTime.
    """
    logger.info(
        f"update_client_expiry: email={email}, extra_days={extra_days}, "
        f"region={region}"
    )

    # Получаем текущий объект чтобы не затереть остальные поля
    current = await get_client(email, region)
    if current:
        client_data = current.get("client", {})
        # Берём реальный expiryTime с панели, если отличается
        real_expire = client_data.get("expiryTime", current_expire_ms)
    else:
        client_data = {}
        real_expire = current_expire_ms

    new_expire = real_expire + extra_days * 86_400_000
    panel = _panels.get(region, _panel)

    # Формируем payload: берём все известные поля из текущего клиента,
    # перезаписываем только expiryTime и limitIp
    payload = {
        "email": email,
        "id": client_data.get("id", client_id),
        "subId": client_data.get("subId", email),
        "flow": client_data.get("flow", "xtls-rprx-vision"),
        "totalGB": client_data.get("totalGB", 0),
        "expiryTime": new_expire,
        "enable": client_data.get("enable", True),
        "tgId": client_data.get("tgId", 0),
        "limitIp": client_data.get("limitIp", devices_limit),
        "comment": client_data.get("comment", ""),
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
    """
    Обновляет лимит IP-адресов клиента.

    ВАЖНО: сначала получаем текущий объект чтобы не затереть expiryTime и др.
    """
    logger.info(f"update_client_ip_limit: email={email}, limit={limit}, region={region}")

    current = await get_client(email, region)
    if current:
        client_data = current.get("client", {})
    else:
        client_data = {}

    panel = _panels.get(region, _panel)
    payload = {
        "email": email,
        "id": client_data.get("id", client_id),
        "subId": client_data.get("subId", email),
        "flow": client_data.get("flow", "xtls-rprx-vision"),
        "totalGB": client_data.get("totalGB", 0),
        "expiryTime": client_data.get("expiryTime", 0),
        "enable": client_data.get("enable", True),
        "tgId": client_data.get("tgId", 0),
        "limitIp": limit,
        "comment": client_data.get("comment", ""),
    }

    data = await panel.post(f"/panel/api/clients/update/{email}", payload)
    result = bool(data and data.get("success"))
    logger.info(f"update_client_ip_limit result: {result}")
    return result


async def get_client_traffic(email: str, region: str = "fi") -> dict | None:
    """
    Возвращает трафик клиента.
    Ответ: {"email": ..., "up": ..., "down": ..., "total": ..., "expiryTime": ...}
    """
    logger.info(f"get_client_traffic: email={email}, region={region}")
    panel = _panels.get(region, _panel)
    data = await panel.get(f"/panel/api/clients/traffic/{email}")
    if data and data.get("success"):
        obj = data.get("obj")
        logger.info(f"Traffic data: {json.dumps(obj)[:200]}")
        return obj
    logger.warning(f"get_client_traffic failed [{region}]: {data}")
    return None


async def get_online_count(email: str, region: str = "fi") -> int | None:
    """
    Возвращает количество активных IP у клиента.
    POST /panel/api/clients/ips/:email → массив "ip (timestamp)" строк.
    """
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
    # Формат "ip (timestamp)\nip (timestamp)\n..."
    ips = [line.strip() for line in str(obj).split("\n") if line.strip()]
    return len(ips)


async def reset_client_traffic(email: str, region: str = "fi") -> bool:
    """
    Сбрасывает счётчик трафика клиента.
    Полезно после продления когда клиент был заблокирован по трафику.
    """
    logger.info(f"reset_client_traffic: email={email}, region={region}")
    panel = _panels.get(region, _panel)
    data = await panel.post(f"/panel/api/clients/resetTraffic/{email}")
    result = bool(data and data.get("success"))
    logger.info(f"reset_client_traffic result: {result}")
    return result


async def delete_client(email: str, region: str = "fi") -> bool:
    """
    Удаляет клиента со всех inbound-ов.
    """
    logger.info(f"delete_client: email={email}, region={region}")
    panel = _panels.get(region, _panel)
    data = await panel.post(f"/panel/api/clients/del/{email}")
    result = bool(data and data.get("success"))
    logger.info(f"delete_client result: {result}")
    return result


async def login() -> bool:
    """Заглушка для обратной совместимости — Bearer Token не требует логина."""
    logger.info("login() called — no-op with Bearer Token auth")
    return True