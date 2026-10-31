from dataclasses import dataclass, field
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()
@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: list[int] = field(default_factory=lambda: [
        int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x
    ])
    REQUIRED_CHANNEL_ID: str = os.getenv("REQUIRED_CHANNEL_ID", "@EscapeTheMatrixVPN")
    REQUIRED_CHANNEL_URL: str = os.getenv("REQUIRED_CHANNEL_URL", "https://t.me/EscapeTheMatrixVPN")
    SITE_URL: str = "https://www.escapethematrix.to"
    FAQ_URL: str = os.getenv("FAQ_URL", "https://telegra.ph/VPN-FAQ-04-12")

    PANEL_HOST: str = os.getenv("PANEL_HOST", "https://pl.escapethematrix.to")
    PANEL_PORT: int = int(os.getenv("PANEL_PORT", "29286"))
    PANEL_USER: str = os.getenv("PANEL_USER", "")
    PANEL_PASS: str = os.getenv("PANEL_PASS", "")
    PANEL_BASE_PATH: str = os.getenv("PANEL_BASE_PATH", "gm7NAKsytzLBGzHxqi")
    PANEL_BASE_URL: str = f"{PANEL_HOST}:{PANEL_PORT}"
    SUB_HOST: str = os.getenv("SUB_HOST", "https://cdn.escapethematrix.to")

    # Новая панель 3x-ui 3.9.0: один клиент привязывается сразу ко всем
    # 9 inbound'ам. Деление на "региональные"/"мобильные" убрано.
    INBOUND_1: int = int(os.getenv("INBOUND_1", "2"))
    INBOUND_2: int = int(os.getenv("INBOUND_2", "3"))
    INBOUND_3: int = int(os.getenv("INBOUND_3", "4"))
    INBOUND_4: int = int(os.getenv("INBOUND_4", "5"))
    INBOUND_5: int = int(os.getenv("INBOUND_5", "6"))
    INBOUND_6: int = int(os.getenv("INBOUND_6", "7"))
    INBOUND_7: int = int(os.getenv("INBOUND_7", "8"))
    INBOUND_8: int = int(os.getenv("INBOUND_8", "9"))
    INBOUND_9: int = int(os.getenv("INBOUND_9", "10"))

    TRIAL_DAYS: int = 3
    MAX_DEVICES: int = 4
    EXTRA_DEVICES: int = 3
    EXTRA_DEVICES_PRICE: int = 60

    REFERRAL_BONUS_PERCENT: float = 25.0

    PLANS: dict = field(default_factory=lambda: {
        "1m": {"days": 30,  "price_rub": 125, "label_ru": "1 месяц",   "label_en": "1 month"},
        "3m": {"days": 90,  "price_rub": 360, "label_ru": "3 месяца",  "label_en": "3 months"},
        "6m": {"days": 180, "price_rub": 700, "label_ru": "6 месяцев", "label_en": "6 months"},
    })

    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET:  str = os.getenv("YOOKASSA_SECRET",  "")

    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    CRYPTOBOT_API_URL: str = "https://pay.crypt.bot/api"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    REDIS_URL: str = os.getenv("REDIS_URL", "")

    MINI_APP_URL: str = os.getenv("MINI_APP_URL", "https://www.escapethematrix.to/miniapp")
    INTERNAL_SECRET: str = os.getenv("INTERNAL_SECRET", "")
    INTERNAL_API_PORT: int = int(os.getenv("INTERNAL_API_PORT", "8000"))

    USE_WEBHOOK: bool = os.getenv("USE_WEBHOOK", "false").lower() == "true"
    WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "https://escapethematrix.to")
    WEBHOOK_PATH: str = "/webhook/bot"
    WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8443"))

    PANEL_API_TOKEN: str = os.getenv("PANEL_API_TOKEN", "")

config = Config()