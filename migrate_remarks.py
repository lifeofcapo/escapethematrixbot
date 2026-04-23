
import asyncio
import json
import aiohttp
from config import config

BASE = f"{config.PANEL_BASE_URL}/{config.PANEL_BASE_PATH}"

async def main():
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as s:
        # Логин
        r = await s.post(f"{BASE}/login",
            json={"username": config.PANEL_USER, "password": config.PANEL_PASS})
        cookies = s.cookie_jar.filter_cookies(BASE)
        cookie_str = "; ".join(f"{k}={v.value}" for k, v in cookies.items())
        headers = {"Cookie": cookie_str, "Content-Type": "application/json"}

        # Получаем всех клиентов inbound
        r = await s.get(f"{BASE}/panel/api/inbounds/get/{config.INBOUND_ID}",
                        headers=headers)
        data = await r.json()
        clients = json.loads(data["obj"]["settings"])["clients"]

        for client in clients:
            client["remark"] = "🇫🇮 Finland Main"
            payload = {
                "id": config.INBOUND_ID,
                "settings": json.dumps({"clients": [client]})
            }
            r = await s.post(
                f"{BASE}/panel/api/inbounds/updateClient/{client['id']}",
                json=payload, headers=headers)
            result = await r.json()
            print(f"{client['email']}: {'✅' if result.get('success') else '❌'}")

asyncio.run(main())