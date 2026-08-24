"""
Пополнение баланса через Telegram Stars.

В отличие от Platega, звёзды не требуют мерчант-аккаунта и обрабатываются
самим Telegram (currency="XTR", provider_token=""), поэтому этот способ
оплаты работает "из коробки" без ключей в .env.

Логика: пользователь выбирает сумму пополнения в рублях (как и в обычном
топ-апе), бот выставляет инвойс в звёздах по курсу STARS_PER_RUB, а после
успешной оплаты на баланс зачисляется та же сумма в рублях, что и при
оплате через Platega — дальше всё (покупка тарифов, реферальные бонусы
и т.д.) работает одинаково независимо от способа пополнения.
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, LabeledPrice

import database as db
import keyboards as kb
from config import TOPUP_AMOUNTS, rub_to_stars
from handlers.profile import _credit_payment

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "topup_stars_menu")
async def cb_topup_stars_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "⭐ <b>Пополнение звёздами</b>\n\n"
        "Выбери сумму — оплата пройдёт прямо внутри Telegram, звёзды "
        "спишутся с твоего баланса Stars:",
        reply_markup=kb.topup_stars_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_stars_"))
async def cb_buy_stars(callback: CallbackQuery):
    rub_amount = int(callback.data.removeprefix("buy_stars_"))
    if rub_amount not in TOPUP_AMOUNTS:
        await callback.answer("Сумма не найдена", show_alert=True)
        return

    stars_amount = rub_to_stars(rub_amount)
    user_id = callback.from_user.id

    await callback.answer()
    try:
        await callback.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Пополнение баланса на {rub_amount}₽",
            description=f"Пополнение баланса PurioVPN на {rub_amount}₽ звёздами Telegram",
            payload=f"stars_topup:{user_id}:{rub_amount}",
            provider_token="",  # для Stars всегда пустой
            currency="XTR",
            prices=[LabeledPrice(label=f"{rub_amount}₽ на баланс", amount=stars_amount)],
        )
    except Exception:
        logger.exception("Не удалось выставить инвойс Stars")
        await callback.message.answer(
            "⚠️ Не получилось выставить счёт в звёздах, попробуй ещё раз чуть позже."
        )


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Оплата звёздами полностью виртуальная — подтверждаем сразу,
    # без внешних проверок (в отличие от Platega тут не может "не пройти").
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment = message.successful_payment
    if payment.currency != "XTR":
        return  # платежи в другой валюте сюда не долетают, но на всякий случай

    user_id = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    # payload вида "stars_topup:<user_id>:<rub_amount>" — берём сумму оттуда,
    # чтобы не зависеть от того, что успело/не успело поменяться в конфиге
    # между выставлением счёта и оплатой.
    try:
        _, _, rub_amount_str = payment.invoice_payload.split(":")
        rub_amount = float(rub_amount_str)
    except Exception:
        logger.exception("Некорректный payload у Stars-платежа: %s", payment.invoice_payload)
        rub_amount = 0.0

    existing = await db.get_payment(charge_id)
    if existing is not None:
        # Защита от повторной обработки одного и того же платежа
        # (Telegram иногда может доставить update повторно).
        return

    await db.create_payment_record(charge_id, user_id, rub_amount)
    await db.add_log(
        user_id, "stars_topup_received",
        details=f"stars={payment.total_amount} rub={rub_amount} charge_id={charge_id}",
    )
    await _credit_payment(charge_id, user_id, rub_amount)

    user = await db.get_user(user_id)
    await message.answer(
        f"✅ Оплата {payment.total_amount}⭐ прошла успешно!\n\n"
        f"Баланс пополнен на {rub_amount:.0f}₽.\n"
        f"💰 Текущий баланс: <b>{user['balance']:.2f}₽</b>",
        reply_markup=kb.back_to_menu_kb(),
    )
