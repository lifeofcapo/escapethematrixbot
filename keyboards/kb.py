from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import config
from locales.texts import t
from services.cryptobot import CRYPTO_ASSETS

# добавить позже нидерландский INBOUND_ID в config.py
REGIONS = {
    "fi": {"flag": "🇫🇮", "name_ru": "Финляндия", "name_en": "Finland",  "available": True},
    # "nl": {"flag": "🇳🇱", "name_ru": "Нидерланды", "name_en": "Netherlands", "available": False},
}


def lang_keyboard(referred_by: int = None) -> InlineKeyboardMarkup:
    def cb(lang: str) -> str:
        return f"lang:{lang}:{referred_by}" if referred_by else f"lang:{lang}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data=cb("ru")),
            InlineKeyboardButton(text="🇬🇧 English", callback_data=cb("en")),
        ]
    ])


def subscribe_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("subscribe_btn", lang),
                              url=config.REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton(text=t("check_sub_btn", lang),
                              callback_data="check_sub")],
    ])


def main_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_profile", lang),   callback_data="menu:profile"),
            InlineKeyboardButton(text=t("btn_subscribe", lang), callback_data="menu:region"),
        ],
        [
            InlineKeyboardButton(text=t("btn_topup", lang),     callback_data="menu:topup"),
            InlineKeyboardButton(text=t("btn_support", lang),   callback_data="menu:support"),
        ],
        [
            InlineKeyboardButton(text=t("btn_faq", lang),       url=config.FAQ_URL),
            InlineKeyboardButton(text=t("btn_site", lang),      url=config.SITE_URL),
        ],
    ])


def profile_keyboard(lang: str) -> InlineKeyboardMarkup:
    mini_app_label = "🌐 Профиль (Mini App)" if lang == "ru" else "🌐 Profile (Mini App)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=mini_app_label,
            web_app=WebAppInfo(url=config.MINI_APP_URL),
        )],
        [InlineKeyboardButton(text=t("btn_devices", lang),     callback_data="menu:devices")],
        [InlineKeyboardButton(text=t("btn_referral", lang),    callback_data="menu:referral")],
        [InlineKeyboardButton(text=t("btn_change_lang", lang), callback_data="menu:change_lang")],
        [InlineKeyboardButton(text=t("btn_back", lang),        callback_data="menu:back")],
    ])


def region_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора региона. Недоступные регионы показываются как 'скоро'."""
    rows = []
    for region_id, region in REGIONS.items():
        flag = region["flag"]
        name = region[f"name_{lang}"]
        if region["available"]:
            rows.append([InlineKeyboardButton(
                text=f"{flag} {name}",
                callback_data=f"region:{region_id}",
            )])
        else:
            # Показываем кнопку, но она недоступна (callback ведёт на заглушку)
            soon = "скоро" if lang == "ru" else "coming soon"
            rows.append([InlineKeyboardButton(
                text=f"{flag} {name} ({soon})",
                callback_data=f"region:soon:{region_id}",
            )])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plans_keyboard(lang: str, region: str = "fi") -> InlineKeyboardMarkup:
    rows = []
    for plan_id, plan in config.PLANS.items():
        label = plan[f"label_{lang}"]
        price = plan["price_rub"]
        rows.append([InlineKeyboardButton(
            text=t("plan_btn", lang, label=label, price=price),
            callback_data=f"buy:{region}:{plan_id}",
        )])
    rows.append([InlineKeyboardButton(
        text=t("extra_devices_btn", lang),
        callback_data="buy:extra_devices",
    )])
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:region")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_purchase_keyboard(lang: str, region: str, plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t("btn_confirm_buy", lang),
            callback_data=f"confirm_buy:{region}:{plan_id}",
        )],
        [InlineKeyboardButton(
            text=t("btn_back", lang),
            callback_data=f"region:{region}",
        )],
    ])


def pay_now_keyboard(lang: str, pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_pay", lang), url=pay_url)],
    ])


def topup_method_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_yookassa", lang),
                              callback_data="topup:yookassa")],
        [InlineKeyboardButton(text=t("btn_crypto", lang),
                              callback_data="topup:crypto_choose")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="menu:back")],
    ])


def topup_crypto_keyboard(lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for asset, label in CRYPTO_ASSETS.items():
        builder.button(text=label, callback_data=f"topup:crypto:{asset}")
    builder.button(text=t("btn_back", lang), callback_data="menu:topup")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def back_keyboard(lang: str, target: str = "menu:back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data=target)]
    ])


def broadcast_confirm_keyboard(lang: str, broadcast_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=t("btn_broadcast_send", lang),
                callback_data=f"broadcast:send:{broadcast_id}",
            ),
            InlineKeyboardButton(
                text=t("btn_broadcast_cancel", lang),
                callback_data=f"broadcast:cancel:{broadcast_id}",
            ),
        ]
    ])