import asyncio
import logging
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, FSInputFile, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from database.db import (
    get_user, get_active_subscription, create_payment, get_payment_by_provider_id,
    mark_payment_paid, create_subscription, extend_subscription,
    update_balance, update_devices_limit, get_balance,
)
from keyboards.kb import (
    plans_keyboard, pay_now_keyboard, back_keyboard,
    topup_method_keyboard, topup_crypto_keyboard, confirm_purchase_keyboard,
    region_keyboard, REGIONS,
    payment_success_keyboard,
)
from locales.texts import t
from services.yookassa import create_yookassa_payment, check_yookassa_payment
from services.cryptobot import create_crypto_invoice, check_crypto_invoice, rub_to_asset
from services.xui import create_client, update_client_ip_limit
from utils.helpers import generate_sub_email

logger = logging.getLogger(__name__)
router = Router()
_PHOTO_FILE_IDS = dict[str, str] = {}
PHOTOS = {
    "menu":    "assets/menu.png",
    "plans":   "assets/plans.jpg",
    "topup":   "assets/topup.png",
    "profile": "assets/profile.jpg",
}


class TopUpStates(StatesGroup):
    waiting_amount = State()


async def _edit_or_answer(callback: CallbackQuery, text: str, reply_markup, photo=None):
    if photo:
        photo_key = None
        # Определяем ключ кэша
        if isinstance(photo, FSInputFile):
            for k, v in PHOTOS.items():
                if v == photo.path:
                    photo_key = k
                    break
            cached = _PHOTO_FILE_IDS.get(photo_key) if photo_key else None
            actual_photo = cached if cached else photo
        else:
            actual_photo = photo
            photo_key = None

        try:
            msg = await callback.message.edit_media(
                media=InputMediaPhoto(media=actual_photo, caption=text, parse_mode="HTML"),
                reply_markup=reply_markup,
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            msg = await callback.message.answer_photo(
                photo=actual_photo, caption=text, reply_markup=reply_markup, parse_mode="HTML"
            )
        
        # Кэшируем file_id
        if photo_key and photo_key not in _PHOTO_FILE_IDS:
            if msg and hasattr(msg, "photo") and msg.photo:
                _PHOTO_FILE_IDS[photo_key] = msg.photo[-1].file_id
    else:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            try:
                await callback.message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
@router.callback_query(F.data == "menu:region")
async def show_region_select(callback: CallbackQuery):
    """Первый шаг при нажатии 'Подписки' — выбор региона."""
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    text = (
        "🌍 <b>Выберите регион сервера:</b>"
        if lang == "ru"
        else "🌍 <b>Choose server region:</b>"
    )
    await _edit_or_answer(callback, text, region_keyboard(lang))
    await callback.answer()


@router.callback_query(F.data.startswith("region:soon:"))
async def region_coming_soon(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    msg = "🚧 Этот сервер пока недоступен. Скоро!" if lang == "ru" else "🚧 This server is not available yet. Coming soon!"
    await callback.answer(msg, show_alert=True)

@router.callback_query(F.data.startswith("region:") & ~F.data.startswith("region:soon:"))
async def show_plans(callback: CallbackQuery):
    region = callback.data.split(":")[1]
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    region_info = REGIONS.get(region)
    if not region_info or not region_info["available"]:
        await callback.answer("❌ Регион недоступен", show_alert=True)
        return

    flag = region_info["flag"]
    region_name = region_info[f"name_{lang}"]
    balance = await get_balance(callback.from_user.id)

    text = t("plans_header", lang, balance=f"{balance:.2f}", region=f"{flag} {region_name}")
    await _edit_or_answer(
        callback,
        text,
        plans_keyboard(lang, region),
        photo=FSInputFile(PHOTOS["plans"]),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:plans")
async def show_plans_legacy(callback: CallbackQuery):
    """Обратная совместимость — команда /plans и прямые ссылки."""
    callback.data = "region:fi"
    await show_plans(callback)

@router.callback_query(F.data.startswith("buy:") & ~F.data.startswith("buy:extra_devices"))
async def buy_plan(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) == 3:
        _, region, plan_id = parts
    else:
        region = "fi"
        plan_id = parts[1]

    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    balance = await get_balance(callback.from_user.id)

    plan = config.PLANS.get(plan_id)
    if not plan:
        await callback.answer()
        return
    price = float(plan["price_rub"])
    plan_label = plan[f"label_{lang}"]

    region_info = REGIONS.get(region, REGIONS["fi"])
    region_str = f"{region_info['flag']} {region_info[f'name_{lang}']}"

    if balance >= price:
        await _edit_or_answer(
            callback,
            t("confirm_purchase", lang,
              plan=plan_label, price=price,
              balance=f"{balance:.2f}", after=f"{balance - price:.2f}",
              region=region_str),
            confirm_purchase_keyboard(lang, region, plan_id),
        )
    else:
        needed = price - balance
        await _edit_or_answer(
            callback,
            t("insufficient_balance", lang,
              price=price, balance=f"{balance:.2f}", needed=f"{needed:.2f}"),
            topup_method_keyboard(lang),
            photo=FSInputFile(PHOTOS["topup"]),
        )
    await callback.answer()


@router.callback_query(F.data == "buy:extra_devices")
async def buy_extra_devices(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    balance = await get_balance(callback.from_user.id)

    sub = await get_active_subscription(callback.from_user.id)
    if not sub:
        await callback.answer(t("no_sub_for_devices", lang), show_alert=True)
        return
    if sub["devices_limit"] >= config.MAX_DEVICES + config.EXTRA_DEVICES:
        await callback.answer(t("already_max_devices", lang), show_alert=True)
        return
    price = float(config.EXTRA_DEVICES_PRICE)
    plan_label = t("extra_devices_btn", lang)

    if balance >= price:
        await _edit_or_answer(
            callback,
            t("confirm_purchase", lang,
              plan=plan_label, price=price,
              balance=f"{balance:.2f}", after=f"{balance - price:.2f}",
              region="—"),
            confirm_purchase_keyboard(lang, "fi", "extra_devices"),
        )
    else:
        needed = price - balance
        await _edit_or_answer(
            callback,
            t("insufficient_balance", lang,
              price=price, balance=f"{balance:.2f}", needed=f"{needed:.2f}"),
            topup_method_keyboard(lang),
            photo=FSInputFile(PHOTOS["topup"]),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_buy:"))
async def confirm_buy(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) == 3:
        _, region, plan_id = parts
    else:
        region = "fi"
        plan_id = parts[1]

    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"

    if plan_id == "extra_devices":
        price = float(config.EXTRA_DEVICES_PRICE)
    else:
        plan = config.PLANS.get(plan_id)
        if not plan:
            await callback.answer()
            return
        price = float(plan["price_rub"])

    balance = await get_balance(callback.from_user.id)
    if balance < price:
        needed = price - balance
        await _edit_or_answer(
            callback,
            t("insufficient_balance", lang,
              price=price, balance=f"{balance:.2f}", needed=f"{needed:.2f}"),
            topup_method_keyboard(lang),
            photo=FSInputFile(PHOTOS["topup"]),
        )
        return

    await update_balance(callback.from_user.id, -price)
    await _activate_plan_balance(bot, callback.from_user.id, plan_id, lang, region)
    await callback.answer()


async def _activate_plan_balance(bot, user_id: int, plan_id: str, lang: str, region: str = "fi"):
    if plan_id == "extra_devices":
        sub = await get_active_subscription(user_id)
        if sub:
            new_limit = sub["devices_limit"] + config.EXTRA_DEVICES
            await update_devices_limit(sub["id"], new_limit)
            await update_client_ip_limit(sub["xui_client_id"], sub["xui_email"], new_limit)
            await bot.send_message(
                user_id,
                f"✅ Лимит устройств увеличен до {new_limit}!" if lang == "ru"
                else f"✅ Device limit increased to {new_limit}!",
            )
        return
 
    plan = config.PLANS.get(plan_id)
    if not plan:
        return
 
    days = plan["days"]
    existing_sub = await get_active_subscription(user_id)
 
    if existing_sub:
        await extend_subscription(existing_sub["id"], days)
        await bot.send_message(
            user_id,
            t("payment_success", lang, sub_link=existing_sub["sub_link"]),
            parse_mode="HTML",
            reply_markup=payment_success_keyboard(lang), 
        )
    else:
        email = generate_sub_email(user_id)
        #todo: при добавлении нидерландского сервера передавать регион в create_client,
        #чтобы использовался нужный inbound
        xui_result = await create_client(email=email, days=days, devices_limit=config.MAX_DEVICES, region=region)
 
        if not xui_result:
            plan_price = float(plan["price_rub"])
            await update_balance(user_id, plan_price)
            await bot.send_message(user_id, t("error_generic", lang))
            logger.error(f"Failed to create xui client for user {user_id}, balance refunded")
            return
 
        sub_link = xui_result["sub_link"]
        await create_subscription(
            user_id=user_id,
            xui_client_id=xui_result["client_id"],
            xui_email=email,
            sub_link=sub_link,
            plan=plan_id,
            days=days,
            devices_limit=config.MAX_DEVICES,
            region=region,
        )
 
        await bot.send_message(
            user_id,
            t("payment_success", lang, sub_link=sub_link),
            parse_mode="HTML",
            reply_markup=payment_success_keyboard(lang), 
        )


@router.callback_query(F.data == "menu:topup")
async def show_topup(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    await _edit_or_answer(
        callback,
        t("topup_header", lang),
        topup_method_keyboard(lang),
        photo=FSInputFile(PHOTOS["topup"]),
    )
    await callback.answer()


@router.callback_query(F.data == "topup:yookassa")
async def topup_yookassa_start(callback: CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    await state.set_state(TopUpStates.waiting_amount)
    await state.update_data(method="yookassa", lang=lang)
    try:
        await callback.message.edit_text(
            t("topup_enter_amount", lang),
            reply_markup=back_keyboard(lang, "menu:topup"),
        )
    except Exception:
        try:
            await callback.message.edit_caption(
                caption=t("topup_enter_amount", lang),
                reply_markup=back_keyboard(lang, "menu:topup"),
            )
        except Exception:
            await callback.message.answer(
                t("topup_enter_amount", lang),
                reply_markup=back_keyboard(lang, "menu:topup"),
            )
    await callback.answer()


@router.callback_query(F.data == "topup:crypto_choose")
async def topup_crypto_start(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    try:
        await callback.message.edit_text(
            t("choose_crypto", lang),
            reply_markup=topup_crypto_keyboard(lang),
        )
    except Exception:
        try:
            await callback.message.edit_caption(
                caption=t("choose_crypto", lang),
                reply_markup=topup_crypto_keyboard(lang),
            )
        except Exception:
            await callback.message.answer(
                t("choose_crypto", lang),
                reply_markup=topup_crypto_keyboard(lang),
            )
    await callback.answer()


@router.callback_query(F.data.startswith("topup:crypto:"))
async def topup_crypto_asset(callback: CallbackQuery, state: FSMContext):
    asset = callback.data.split(":")[2]
    user = await get_user(callback.from_user.id)
    lang = user.get("language", "ru") if user else "ru"
    await state.set_state(TopUpStates.waiting_amount)
    await state.update_data(method="cryptobot", asset=asset, lang=lang)
    try:
        await callback.message.edit_text(
            t("topup_enter_amount", lang),
            reply_markup=back_keyboard(lang, "menu:topup"),
        )
    except Exception:
        await callback.message.answer(
            t("topup_enter_amount", lang),
            reply_markup=back_keyboard(lang, "menu:topup"),
        )
    await callback.answer()


@router.message(TopUpStates.waiting_amount)
async def topup_process_amount(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")

    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount < 50:
            await message.answer(t("topup_min_error", lang))
            return
    except ValueError:
        await message.answer(t("invalid_input", lang))
        return

    await state.clear()
    method = data.get("method", "yookassa")

    if method == "yookassa":
        result = await create_yookassa_payment(
            amount_rub=amount,
            description="Balance top-up | EscapeTheMatrix VPN",
            metadata={"user_id": str(message.from_user.id), "plan": "topup"},
        )
        if not result:
            await message.answer(t("error_generic", lang))
            return
        await create_payment(
            user_id=message.from_user.id, amount=amount, currency="RUB",
            provider="yookassa", provider_id=result["payment_id"], purpose="topup",
        )
        await message.answer(
            t("payment_created", lang, amount=f"{amount} ₽"),
            reply_markup=pay_now_keyboard(lang, result["confirmation_url"]),
            parse_mode="HTML",
        )
        asyncio.create_task(
            _poll_topup_yookassa(bot, message.from_user.id, result["payment_id"], amount, lang)
        )
    else:
        asset = data.get("asset", "USDT")
        crypto_amount = await rub_to_asset(amount, asset)
        result = await create_crypto_invoice(
            asset=asset,
            amount_usd=crypto_amount,
            description="Balance top-up | EscapeTheMatrix VPN",
            payload=f"{message.from_user.id}:topup",
        )
        if not result:
            await message.answer(t("error_generic", lang))
            return
        await create_payment(
            user_id=message.from_user.id, amount=amount, currency="RUB",
            provider="cryptobot", provider_id=result["invoice_id"], purpose="topup",
        )
        await message.answer(
            t("payment_created", lang, amount=f"{crypto_amount} {asset}"),
            reply_markup=pay_now_keyboard(lang, result["pay_url"]),
            parse_mode="HTML",
        )
        asyncio.create_task(
            _poll_topup_crypto(bot, message.from_user.id, result["invoice_id"], amount, lang)
        )


async def _poll_topup_yookassa(bot: Bot, user_id: int, payment_id: str,
                                amount: float, lang: str):
    intervals = [20, 20, 30, 30, 60] + [60] * 20
    for delay in intervals:
        await asyncio.sleep(delay)
        status = await check_yookassa_payment(payment_id)
        if status == "succeeded":
            payment = await get_payment_by_provider_id(payment_id)
            if payment and payment["status"] != "paid":
                await mark_payment_paid(payment_id)
                await update_balance(user_id, amount)
                balance = await get_balance(user_id)
                await bot.send_message(
                    user_id,
                    t("balance_topped", lang, amount=amount, balance=f"{balance:.2f}"),
                )
            return
        elif status in ("canceled", "failed"):
            return


async def _poll_topup_crypto(bot: Bot, user_id: int, invoice_id: str,
                              amount: float, lang: str, attempts: int = 30):
    for _ in range(attempts):
        await asyncio.sleep(60)
        status = await check_crypto_invoice(invoice_id)
        if status == "paid":
            payment = await get_payment_by_provider_id(invoice_id)
            if payment and payment["status"] != "paid":
                await mark_payment_paid(invoice_id)
                await update_balance(user_id, amount)
                balance = await get_balance(user_id)
                await bot.send_message(
                    user_id,
                    t("balance_topped", lang, amount=amount, balance=f"{balance:.2f}"),
                )
            return
        elif status == "expired":
            return