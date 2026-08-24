"""
Профиль пользователя и баланс. Пополнение — через Platega (СБП), пользователь
сам вводит сумму текстом (без выбора из фиксированного списка).
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import platega_client
from config import ENABLE_PLATEGA, TOPUP_MIN_AMOUNT, TOPUP_MAX_AMOUNT
from states import TopUpBalance

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "menu_profile")
async def cb_profile(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    username = callback.from_user.username
    name_display = f"@{username}" if username else callback.from_user.full_name

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"Ник: {name_display}\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"💰 Баланс: <b>{user['balance']:.2f}₽</b>\n"
    )
    await callback.message.edit_text(text, reply_markup=kb.profile_kb())
    await callback.answer()


@router.callback_query(F.data == "menu_balance")
async def cb_balance(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = (
        f"💰 <b>Баланс</b>\n\n"
        f"Текущий баланс: <b>{user['balance']:.2f}₽</b>\n\n"
        f"Нажми «Пополнить баланс», чтобы указать сумму и оплатить через СБП."
    )
    await callback.message.edit_text(text, reply_markup=kb.balance_kb())
    await callback.answer()


@router.callback_query(F.data == "topup_start")
async def cb_topup_start(callback: CallbackQuery, state: FSMContext):
    if not ENABLE_PLATEGA:
        await db.add_log(callback.from_user.id, "topup_attempt_no_payment_configured")
        await callback.answer("🚧 Оплата ещё в разработке", show_alert=True)
        await callback.message.edit_text(
            "🚧 <b>Пополнение баланса скоро будет доступно</b>\n\n"
            "Мы уже подключаем оплату — совсем немного осталось. "
            "Загляни чуть позже 🙌",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    await state.set_state(TopUpBalance.waiting_amount)
    await callback.message.edit_text(
        f"💳 Введи сумму пополнения в рублях (от {TOPUP_MIN_AMOUNT:.0f}₽ до "
        f"{TOPUP_MAX_AMOUNT:.0f}₽):",
        reply_markup=kb.topup_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "topup_cancel")
async def cb_topup_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    text = (
        f"💰 <b>Баланс</b>\n\n"
        f"Текущий баланс: <b>{user['balance']:.2f}₽</b>\n\n"
        f"Нажми «Пополнить баланс», чтобы указать сумму и оплатить через СБП."
    )
    await callback.message.edit_text(text, reply_markup=kb.balance_kb())
    await callback.answer("Отменено")


@router.message(TopUpBalance.waiting_amount)
async def topup_amount_entered(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer(
            "Нужно прислать число, например 500. Попробуй ещё раз:",
            reply_markup=kb.topup_cancel_kb(),
        )
        return

    if amount < TOPUP_MIN_AMOUNT or amount > TOPUP_MAX_AMOUNT:
        await message.answer(
            f"Сумма должна быть от {TOPUP_MIN_AMOUNT:.0f}₽ до {TOPUP_MAX_AMOUNT:.0f}₽. "
            f"Попробуй ещё раз:",
            reply_markup=kb.topup_cancel_kb(),
        )
        return

    await state.clear()
    user_id = message.from_user.id
    amount = round(amount, 2)

    try:
        transaction_id, pay_url = await platega_client.create_payment(
            amount=amount,
            description=f"Пополнение баланса PurioVPN на {amount:.0f}₽ (ID {user_id})",
            payload=str(user_id),
        )
    except Exception:
        logger.exception("Ошибка создания платежа Platega")
        await message.answer(
            "Платёжная система временно недоступна, попробуй позже 🙁",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    await db.create_payment_record(transaction_id, user_id, amount)
    await db.add_log(user_id, "topup_created", details=f"amount={amount} transaction_id={transaction_id}")

    await message.answer(
        f"💳 Счёт на <b>{amount:.0f}₽</b> создан.\n\n"
        f"Нажми «Оплатить», заверши оплату через СБП, затем вернись и нажми "
        f"«Проверить оплату» (либо бот сам зачислит в течение минуты).",
        reply_markup=kb.payment_link_kb(pay_url, transaction_id),
    )


@router.callback_query(F.data.startswith("check_pay_"))
async def cb_check_payment(callback: CallbackQuery):
    transaction_id = callback.data.removeprefix("check_pay_")
    payment = await db.get_payment(transaction_id)

    if payment is None:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment["status"] == "succeeded":
        await callback.answer("Этот платёж уже зачислен ✅", show_alert=True)
        return

    try:
        status = await platega_client.check_payment_status(transaction_id)
    except Exception:
        logger.exception("Ошибка проверки статуса платежа Platega")
        await callback.answer("Не получилось проверить оплату, попробуй ещё раз чуть позже", show_alert=True)
        return

    if status == "CONFIRMED":
        await _credit_payment(transaction_id, payment["user_id"], payment["amount"])
        await callback.answer("Оплата прошла! Баланс пополнен ✅", show_alert=True)
        user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            f"✅ Баланс успешно пополнен на {payment['amount']:.0f}₽.\n\n"
            f"💰 Текущий баланс: <b>{user['balance']:.2f}₽</b>",
            reply_markup=kb.back_to_menu_kb(),
        )
    elif status in ("CANCELED", "CHARGEBACKED"):
        await db.mark_payment(transaction_id, "canceled")
        await callback.answer("Платёж отменён", show_alert=True)
    else:
        await callback.answer("Оплата пока не поступила, попробуй проверить через минуту", show_alert=True)


async def _credit_payment(transaction_id: str, user_id: int, amount: float):
    """
    Зачисляет оплату на баланс. Реферальные 7% начисляются не за само
    пополнение, а за покупку подписки (см. handlers/subscription.py) —
    поведение одинаково для любого источника денег на балансе.
    """
    await db.mark_payment(transaction_id, "succeeded")
    await db.update_balance(user_id, amount)
    await db.add_log(user_id, "topup_succeeded", details=f"amount={amount} transaction_id={transaction_id}")
