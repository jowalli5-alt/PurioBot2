"""
Промокоды: пользователь вводит код текстом, бот проверяет его в БД
(активен ли, не исчерпан ли лимит активаций, не использовал ли уже этот
пользователь) и, если всё ок, начисляет дни подписки — так же, как при
обычной покупке (через Remnawave), чтобы реальный VPN-доступ выдавался
сразу, а не только "дни" в базе.
"""
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

import database as db
import keyboards as kb
import remnawave_client
from states import PromoActivate

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "promo_enter_start")
async def cb_promo_enter_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoActivate.waiting_code)
    await callback.message.edit_text(
        "🎟 Введи промокод:",
        reply_markup=kb.promo_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "promo_cancel")
async def cb_promo_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Отменено.", reply_markup=kb.back_to_menu_kb()
    )
    await callback.answer()


@router.message(PromoActivate.waiting_code)
async def promo_activate(message: Message, state: FSMContext):
    await state.clear()
    code = (message.text or "").strip().upper()
    user_id = message.from_user.id

    if not code:
        await message.answer("Пустой промокод. Попробуй ещё раз из меню.", reply_markup=kb.back_to_menu_kb())
        return

    promo = await db.get_promo_code(code)
    if promo is None or not promo["active"]:
        await message.answer(
            "❌ Такого промокода не существует или он больше не действует.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    if promo["used_count"] >= promo["max_activations"]:
        await message.answer(
            "❌ У этого промокода закончились активации.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    if await db.has_user_used_promo(code, user_id):
        await message.answer(
            "❌ Ты уже использовал этот промокод раньше.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    activated = await db.activate_promo_code(code, user_id)
    if not activated:
        # Гонка: кто-то успел использовать код между проверками выше.
        await message.answer(
            "❌ Не получилось активировать промокод (возможно, ты уже его использовал).",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    days = promo["days"]

    try:
        subscription_url = await remnawave_client.provision_subscription(user_id, days)
    except Exception:
        logger.exception("Не удалось выдать подписку по промокоду через Remnawave")
        await db.extend_subscription(user_id, days)
        await db.add_log(user_id, "promo_activated_no_remnawave", details=f"code={code} days={days}")
        await message.answer(
            f"✅ Промокод активирован, начислено {days} дн. подписки.\n\n"
            f"⚠️ Не получилось автоматически выдать ссылку доступа — "
            f"напиши в поддержку, выдадим вручную.",
            reply_markup=kb.back_to_menu_kb(),
        )
        return

    new_expire = await db.extend_subscription(
        user_id, days, subscription_url=subscription_url, tariff_days=days, price=0,
    )
    await db.add_log(user_id, "promo_activated", details=f"code={code} days={days}")

    dt = datetime.fromtimestamp(new_expire)
    await message.answer(
        f"✅ Промокод активирован! Начислено <b>{days}</b> дн. подписки.\n"
        f"Подписка активна до <b>{dt.strftime('%d.%m.%Y')}</b>.\n\n"
        f"🔗 Твоя ссылка для подключения:\n<code>{subscription_url}</code>",
        reply_markup=kb.back_to_menu_kb(),
    )
