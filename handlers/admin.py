"""
Админ-панель: выдать баланс, рассылка сообщений, список пользователей, статистика.
Доступна только ID из config.ADMIN_IDS.
"""
import asyncio
import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import database as db
import keyboards as kb
import remnawave_client
from config import ADMIN_IDS
from states import (
    AdminGiveBalance, AdminBroadcast, AdminMessageUser, AdminFindUser, AdminTicketReply,
    AdminCreatePromo, AdminGiveSubscription, AdminRevokeSubscription,
)

logger = logging.getLogger(__name__)
router = Router()


def admin_only(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.callback_query(F.data == "menu_admin")
async def cb_admin_menu(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("⚙️ <b>Админ-панель</b>", reply_markup=kb.admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "admin_cancel")
async def cb_admin_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("⚙️ <b>Админ-панель</b>", reply_markup=kb.admin_menu_kb())
    await callback.answer("Отменено")


# ---------------- Выдать баланс ----------------

@router.callback_query(F.data == "admin_give_balance")
async def cb_give_balance_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminGiveBalance.waiting_user_id)
    await callback.message.edit_text(
        "Введи Telegram ID пользователя, которому нужно начислить баланс:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminGiveBalance.waiting_user_id)
async def admin_give_balance_userid(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно прислать число (Telegram ID). Попробуй ещё раз:")
        return

    user = await db.get_user(target_id)
    if user is None:
        await message.answer(
            "Пользователь с таким ID не найден в базе (он ни разу не запускал бота). "
            "Проверь ID и попробуй ещё раз:"
        )
        return

    await state.update_data(target_id=target_id)
    await state.set_state(AdminGiveBalance.waiting_amount)
    await message.answer(
        f"Пользователь найден: {user['username'] or user['full_name']} "
        f"(текущий баланс {user['balance']:.2f}₽).\n\n"
        f"Введи сумму для начисления (можно отрицательную, чтобы списать):"
    )


@router.message(AdminGiveBalance.waiting_amount)
async def admin_give_balance_amount(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer("Нужно число, например 500 или -100. Попробуй ещё раз:")
        return

    data = await state.get_data()
    target_id = data["target_id"]

    await db.update_balance(target_id, amount)
    await db.add_log(target_id, "admin_balance_change", details=f"amount={amount} by={message.from_user.id}")
    await state.clear()

    user = await db.get_user(target_id)
    await message.answer(
        f"✅ Готово. Новый баланс пользователя {target_id}: <b>{user['balance']:.2f}₽</b>",
        reply_markup=kb.admin_menu_kb(),
    )

    try:
        await message.bot.send_message(
            target_id,
            f"💰 Тебе начислено {amount:+.2f}₽ администратором.\nТекущий баланс: {user['balance']:.2f}₽",
        )
    except (TelegramForbiddenError, Exception):
        pass


# ---------------- Найти пользователя (карточка + рефералы) ----------------

@router.callback_query(F.data == "admin_find_user")
async def cb_find_user_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminFindUser.waiting_user_id)
    await callback.message.edit_text(
        "Введи Telegram ID пользователя, чтобы посмотреть его карточку "
        "(баланс, подписку, рефералов):",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminFindUser.waiting_user_id)
async def admin_find_user_show(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно прислать число (Telegram ID). Попробуй ещё раз:")
        return

    await state.clear()
    user = await db.get_user(target_id)
    if user is None:
        await message.answer(
            "Пользователь с таким ID не найден в базе.", reply_markup=kb.admin_menu_kb()
        )
        return

    expire = "—"
    if user["subscription_expire"]:
        expire = datetime.fromtimestamp(user["subscription_expire"]).strftime("%d.%m.%Y %H:%M")
    link_line = f"\n🔗 <code>{user['subscription_url']}</code>" if user["subscription_url"] else ""
    invited_by = f"\n👤 Пригласил: <code>{user['referrer_id']}</code>" if user["referrer_id"] else ""

    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Юзернейм: @{user['username'] or '—'}\n"
        f"💰 Баланс: <b>{user['balance']:.2f}₽</b>{invited_by}\n\n"
        f"🔑 Подписка до: {expire}{link_line}\n\n"
        f"🤝 <b>Рефералы</b>\n"
        f"Всего приглашено: <b>{user['referral_count']}</b>\n"
        f"Из них купили подписку: <b>{user['referral_paid_count']}</b>\n"
        f"Заработано с рефералов: <b>{user['referral_earned']:.2f}₽</b>"
    )
    await message.answer(text, reply_markup=kb.admin_menu_kb())


# ---------------- Подписка: выдать / забрать ----------------

@router.callback_query(F.data == "admin_subscription_menu")
async def cb_admin_subscription_menu(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await callback.message.edit_text(
        "🔑 <b>Подписка пользователя</b>\n\nВыбери действие:",
        reply_markup=kb.admin_subscription_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_give_subscription")
async def cb_admin_give_subscription_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminGiveSubscription.waiting_user_id)
    await callback.message.edit_text(
        "Введи Telegram ID пользователя, которому нужно выдать подписку:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminGiveSubscription.waiting_user_id)
async def admin_give_subscription_userid(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно прислать число (Telegram ID). Попробуй ещё раз:")
        return

    user = await db.get_user(target_id)
    if user is None:
        await message.answer(
            "Пользователь с таким ID не найден в базе (он ни разу не запускал бота). "
            "Проверь ID и попробуй ещё раз:"
        )
        return

    await state.update_data(target_id=target_id)
    await state.set_state(AdminGiveSubscription.waiting_days)
    await message.answer(
        f"Пользователь найден: {user['username'] or user['full_name']}.\n\n"
        f"На сколько дней выдать/продлить подписку? Введи число, например 30:"
    )


@router.message(AdminGiveSubscription.waiting_days)
async def admin_give_subscription_days(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно положительное целое число дней, например 30. Попробуй ещё раз:")
        return

    data = await state.get_data()
    target_id = data["target_id"]
    await state.clear()

    try:
        subscription_url = await remnawave_client.provision_subscription(target_id, days)
    except Exception:
        logger.exception("Не удалось выдать подписку через Remnawave (админ-выдача)")
        new_expire = await db.extend_subscription(target_id, days)
        await db.add_log(
            target_id, "admin_subscription_given_no_remnawave",
            details=f"days={days} by={message.from_user.id}",
        )
        await message.answer(
            f"⚠️ Дни начислены в базе ({days} дн.), но выдать доступ через Remnawave "
            f"не получилось — выдай ссылку вручную.",
            reply_markup=kb.admin_menu_kb(),
        )
        return

    new_expire = await db.extend_subscription(
        target_id, days, subscription_url=subscription_url, tariff_days=days, price=0,
    )
    await db.add_log(
        target_id, "admin_subscription_given",
        details=f"days={days} by={message.from_user.id}",
    )

    dt = datetime.fromtimestamp(new_expire)
    await message.answer(
        f"✅ Подписка выдана пользователю {target_id} до <b>{dt.strftime('%d.%m.%Y')}</b>.",
        reply_markup=kb.admin_menu_kb(),
    )
    try:
        await message.bot.send_message(
            target_id,
            f"🎁 Тебе выдана подписка на {days} дн. администратором!\n\n"
            f"🔗 Ссылка для подключения:\n<code>{subscription_url}</code>",
        )
    except (TelegramForbiddenError, Exception):
        pass


@router.callback_query(F.data == "admin_revoke_subscription")
async def cb_admin_revoke_subscription_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminRevokeSubscription.waiting_user_id)
    await callback.message.edit_text(
        "Введи Telegram ID пользователя, у которого нужно забрать подписку:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminRevokeSubscription.waiting_user_id)
async def admin_revoke_subscription_userid(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно прислать число (Telegram ID). Попробуй ещё раз:")
        return

    await state.clear()
    user = await db.get_user(target_id)
    if user is None:
        await message.answer(
            "Пользователь с таким ID не найден в базе.", reply_markup=kb.admin_menu_kb()
        )
        return

    disabled = await remnawave_client.disable_user(target_id)
    await db.revoke_subscription(target_id)
    await db.add_log(
        target_id, "admin_subscription_revoked",
        details=f"by={message.from_user.id} remnawave_disabled={disabled}",
    )

    note = "" if disabled else "\n⚠️ Не удалось отключить доступ в Remnawave — проверь вручную."
    await message.answer(
        f"✅ Подписка пользователя {target_id} аннулирована.{note}",
        reply_markup=kb.admin_menu_kb(),
    )
    try:
        await message.bot.send_message(
            target_id, "🔒 Твоя подписка на VPN была аннулирована администратором.",
        )
    except (TelegramForbiddenError, Exception):
        pass


# ---------------- Рассылка ----------------

@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminBroadcast.waiting_text)
    await callback.message.edit_text(
        "Пришли текст сообщения, которое нужно разослать всем пользователям бота:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminBroadcast.waiting_text)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    await state.clear()
    text = message.html_text
    user_ids = await db.get_all_user_ids()

    status_msg = await message.answer(f"📣 Начинаю рассылку на {len(user_ids)} пользователей...")

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await message.bot.send_message(uid, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await message.bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # чтобы не упереться в лимиты Telegram

    await db.add_log(message.from_user.id, "admin_broadcast", details=f"sent={sent} failed={failed}")
    await status_msg.edit_text(
        f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}",
        reply_markup=kb.admin_menu_kb(),
    )


# ---------------- Написать одному пользователю (по ID) ----------------

@router.callback_query(F.data == "admin_message_user")
async def cb_message_user_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminMessageUser.waiting_user_id)
    await callback.message.edit_text(
        "Введи Telegram ID пользователя, которому нужно написать:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminMessageUser.waiting_user_id)
async def admin_message_user_id(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        target_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно прислать число (Telegram ID). Попробуй ещё раз:")
        return

    user = await db.get_user(target_id)
    if user is None:
        await message.answer(
            "Пользователь с таким ID не найден в базе (он ни разу не запускал бота). "
            "Проверь ID и попробуй ещё раз:"
        )
        return

    await state.update_data(target_id=target_id)
    await state.set_state(AdminMessageUser.waiting_text)
    await message.answer(
        f"Пользователь найден: {user['username'] or user['full_name']}.\n\n"
        f"Введи текст сообщения:"
    )


@router.message(AdminMessageUser.waiting_text)
async def admin_message_user_text(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()
    target_id = data["target_id"]
    await state.clear()

    try:
        await message.bot.send_message(target_id, message.html_text)
        await db.add_log(target_id, "admin_direct_message", details=f"by={message.from_user.id}")
        await message.answer("✅ Сообщение отправлено.", reply_markup=kb.admin_menu_kb())
    except TelegramForbiddenError:
        await message.answer(
            "⚠️ Не получилось отправить — пользователь заблокировал бота.",
            reply_markup=kb.admin_menu_kb(),
        )
    except Exception:
        logger.exception("Ошибка отправки личного сообщения пользователю")
        await message.answer(
            "⚠️ Не получилось отправить сообщение, попробуй ещё раз.",
            reply_markup=kb.admin_menu_kb(),
        )


# ---------------- Тикеты поддержки ----------------

@router.callback_query(F.data.startswith("admin_tickets_"))
async def cb_admin_tickets(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    offset = int(callback.data.removeprefix("admin_tickets_"))
    tickets = await db.list_open_tickets(limit=5, offset=offset)
    total = await db.count_open_tickets()

    if not tickets:
        await callback.message.edit_text(
            "🎫 Открытых тикетов нет.", reply_markup=kb.admin_menu_kb()
        )
        await callback.answer()
        return

    has_more = offset + len(tickets) < total
    await callback.message.edit_text(
        f"🎫 <b>Тикеты</b> (открытых: {total})\n\n"
        f"🆕 — новый, ждёт ответа. 💬 — уже отвечен, можно закрыть.",
        reply_markup=kb.admin_tickets_list_kb(tickets, offset, has_more),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_view_"))
async def cb_admin_ticket_view(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    ticket_id = int(callback.data.removeprefix("admin_ticket_view_"))
    ticket = await db.get_ticket(ticket_id)
    if ticket is None:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    messages = await db.get_ticket_messages(ticket_id)
    lines = [f"🎫 <b>Тикет #{ticket_id}</b> — статус: {ticket['status']}\n"]
    for m in messages:
        dt = datetime.fromtimestamp(m["created_at"]).strftime("%d.%m %H:%M")
        who = "👤 Пользователь" if m["sender"] == "user" else "🛠 Админ"
        lines.append(f"{who} ({dt}):\n{m['text']}\n")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb.admin_ticket_view_kb(ticket_id, ticket["status"])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ticket_reply_"))
async def cb_admin_ticket_reply_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    ticket_id = int(callback.data.removeprefix("admin_ticket_reply_"))
    ticket = await db.get_ticket(ticket_id)
    if ticket is None:
        await callback.answer("Тикет не найден", show_alert=True)
        return

    await state.update_data(ticket_id=ticket_id, ticket_user_id=ticket["user_id"])
    await state.set_state(AdminTicketReply.waiting_text)
    await callback.message.edit_text(
        f"✍️ Введи ответ для тикета #{ticket_id} "
        f"(пользователю <code>{ticket['user_id']}</code>):",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminTicketReply.waiting_text)
async def admin_ticket_reply_send(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    data = await state.get_data()
    ticket_id = data["ticket_id"]
    target_id = data["ticket_user_id"]
    await state.clear()

    reply_text = message.text or message.caption or ""
    await db.add_ticket_message(ticket_id, "admin", message.from_user.id, reply_text)
    await db.add_log(target_id, "ticket_answered", details=f"ticket_id={ticket_id} by={message.from_user.id}")

    try:
        await message.bot.send_message(
            target_id,
            f"🎫 Ответ по твоему тикету #{ticket_id}:\n\n{reply_text}",
        )
        sent_note = "✅ Ответ отправлен пользователю."
    except (TelegramForbiddenError, Exception):
        sent_note = "⚠️ Ответ сохранён, но отправить пользователю не удалось (заблокировал бота?)."

    ticket = await db.get_ticket(ticket_id)
    await message.answer(
        sent_note, reply_markup=kb.admin_ticket_view_kb(ticket_id, ticket["status"])
    )


@router.callback_query(F.data.startswith("admin_ticket_close_"))
async def cb_admin_ticket_close(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    ticket_id = int(callback.data.removeprefix("admin_ticket_close_"))
    await db.close_ticket(ticket_id)
    await db.add_log(callback.from_user.id, "ticket_closed", details=f"ticket_id={ticket_id}")
    await callback.answer("Тикет закрыт ✅")

    await callback.message.edit_text(
        f"🎫 Тикет #{ticket_id} закрыт.", reply_markup=kb.admin_menu_kb()
    )


# ---------------- Список пользователей ----------------

@router.callback_query(F.data.startswith("admin_users_"))
async def cb_admin_users(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    offset = int(callback.data.removeprefix("admin_users_"))
    users = await db.list_users(limit=10, offset=offset)
    total = await db.count_all_users()

    if not users:
        await callback.message.edit_text("Пользователей пока нет.", reply_markup=kb.admin_menu_kb())
        await callback.answer()
        return

    lines = [f"👥 <b>Пользователи</b> (показано {offset + 1}-{offset + len(users)} из {total})\n"]
    for u in users:
        expire = "—"
        if u["subscription_expire"]:
            expire = datetime.fromtimestamp(u["subscription_expire"]).strftime("%d.%m.%Y")
        ref = f", пригласил {u['referrer_id']}" if u["referrer_id"] else ""
        lines.append(
            f"• <code>{u['user_id']}</code> @{u['username'] or '—'} | "
            f"баланс {u['balance']:.0f}₽ | до {expire}{ref}"
        )

    has_more = offset + len(users) < total
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb.admin_users_pagination_kb(offset, has_more)
    )
    await callback.answer()


# ---------------- Промокоды ----------------

@router.callback_query(F.data == "admin_promo_menu")
async def cb_admin_promo_menu(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await callback.message.edit_text("🎟 <b>Промокоды</b>", reply_markup=kb.admin_promo_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "admin_promo_create")
async def cb_admin_promo_create_start(callback: CallbackQuery, state: FSMContext):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await state.set_state(AdminCreatePromo.waiting_code)
    await callback.message.edit_text(
        "Напиши текст промокода (например: SUMMER2026). "
        "Буквы/цифры, без пробелов:",
        reply_markup=kb.cancel_fsm_kb(),
    )
    await callback.answer()


@router.message(AdminCreatePromo.waiting_code)
async def admin_promo_create_code(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    code = (message.text or "").strip().upper().replace(" ", "")
    if not code:
        await message.answer("Промокод не может быть пустым. Попробуй ещё раз:")
        return

    existing = await db.get_promo_code(code)
    if existing is not None:
        await message.answer(
            f"⚠️ Промокод <code>{code}</code> уже существует. Введи другой текст:"
        )
        return

    await state.update_data(code=code)
    await state.set_state(AdminCreatePromo.waiting_days)
    await message.answer(
        f"Код: <code>{code}</code>.\n\nНа сколько дней подписки он будет действовать? "
        f"Введи число, например 30:"
    )


@router.message(AdminCreatePromo.waiting_days)
async def admin_promo_create_days(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно положительное целое число дней, например 30. Попробуй ещё раз:")
        return

    await state.update_data(days=days)
    await state.set_state(AdminCreatePromo.waiting_activations)
    await message.answer(
        f"Дней: <b>{days}</b>.\n\nСколько раз промокод можно активировать всего? "
        f"Введи число, например 50:"
    )


@router.message(AdminCreatePromo.waiting_activations)
async def admin_promo_create_activations(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return

    try:
        max_activations = int(message.text.strip())
        if max_activations <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно положительное целое число активаций, например 50. Попробуй ещё раз:")
        return

    data = await state.get_data()
    code = data["code"]
    days = data["days"]
    await state.clear()

    created = await db.create_promo_code(code, days, max_activations, message.from_user.id)
    if not created:
        await message.answer(
            f"⚠️ Не получилось создать — промокод <code>{code}</code> уже существует.",
            reply_markup=kb.admin_promo_menu_kb(),
        )
        return

    await db.add_log(
        message.from_user.id, "admin_promo_created",
        details=f"code={code} days={days} max_activations={max_activations}",
    )
    await message.answer(
        f"✅ Промокод создан:\n\n"
        f"Код: <code>{code}</code>\n"
        f"Дней подписки: <b>{days}</b>\n"
        f"Активаций: <b>{max_activations}</b>\n\n"
        f"Отправь его пользователям — они вводят его через кнопку «🎟 Промокод» в главном меню.",
        reply_markup=kb.admin_promo_menu_kb(),
    )


@router.callback_query(F.data.startswith("admin_promo_list_"))
async def cb_admin_promo_list(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    offset = int(callback.data.removeprefix("admin_promo_list_"))
    promos = await db.list_promo_codes(limit=10, offset=offset)
    total = await db.count_promo_codes()

    if not promos:
        await callback.message.edit_text(
            "Промокодов пока нет.", reply_markup=kb.admin_promo_menu_kb()
        )
        await callback.answer()
        return

    lines = [f"📋 <b>Промокоды</b> (показано {offset + 1}-{offset + len(promos)} из {total})\n"]
    for p in promos:
        mark = "✅" if p["active"] and p["used_count"] < p["max_activations"] else "⛔️"
        lines.append(
            f"{mark} <code>{p['code']}</code> — {p['days']} дн., "
            f"активаций {p['used_count']}/{p['max_activations']}"
        )

    has_more = offset + len(promos) < total
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb.admin_promo_list_kb(offset, has_more)
    )
    await callback.answer()


# ---------------- История платежей ----------------

@router.callback_query(F.data.startswith("admin_payments_"))
async def cb_admin_payments(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    offset = int(callback.data.removeprefix("admin_payments_"))
    payments = await db.list_payments(limit=10, offset=offset)
    total = await db.count_payments()

    if not payments:
        await callback.message.edit_text("Платежей пока нет.", reply_markup=kb.admin_menu_kb())
        await callback.answer()
        return

    status_marks = {"succeeded": "✅", "pending": "⏳", "canceled": "❌"}
    lines = [f"🧾 <b>История платежей</b> (показано {offset + 1}-{offset + len(payments)} из {total})\n"]
    for p in payments:
        dt = datetime.fromtimestamp(p["created_at"]).strftime("%d.%m.%Y %H:%M")
        mark = status_marks.get(p["status"], "•")
        lines.append(
            f"{mark} <code>{p['id']}</code> — {p['user_id']} — {p['amount']:.0f}₽ — {dt}"
        )

    has_more = offset + len(payments) < total
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb.admin_payments_list_kb(offset, has_more)
    )
    await callback.answer()


# ---------------- Серверы / мониторинг ----------------

@router.callback_query(F.data == "admin_servers")
async def cb_admin_servers(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    await callback.answer("Собираю данные с серверов⏳")

    nodes = await remnawave_client.get_nodes_stats()
    system_stats = await remnawave_client.get_system_stats()

    lines = ["🖥 <b>Серверы</b>\n"]

    if system_stats:
        lines.append(
            f"📡 <b>Мониторинг (всего)</b>\n"
            f"Пользователей онлайн: <b>{system_stats.get('users_online') if system_stats.get('users_online') is not None else '—'}</b>\n"
            f"Пользователей всего: <b>{system_stats.get('users_total') if system_stats.get('users_total') is not None else '—'}</b>\n"
            f"Трафик суммарно: <b>{remnawave_client.format_bytes(system_stats.get('traffic_bytes'))}</b>\n"
        )

    if not nodes:
        lines.append("⚠️ Не удалось получить список серверов (проверь подключение к Remnawave).")
    else:
        for node in nodes:
            status = "🟢 онлайн" if node["online"] else ("🔴 офлайн" if node["online"] is False else "⚪️ неизвестно")
            ping = f"{node['ping_ms']} мс" if node["ping_ms"] is not None else "—"
            users = node["users_online"] if node["users_online"] is not None else "—"
            traffic = remnawave_client.format_bytes(node["traffic_bytes"])
            lines.append(
                f"\n<b>{node['name']}</b> — {status}\n"
                f"👥 Пользователей: {users}\n"
                f"📶 Трафик: {traffic}\n"
                f"🏓 Пинг: {ping}"
            )

    await callback.message.edit_text("\n".join(lines), reply_markup=kb.admin_servers_kb())


# ---------------- Статистика ----------------

@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not admin_only(callback.from_user.id):
        return await callback.answer("Доступ запрещён", show_alert=True)

    total = await db.count_all_users()
    logs = await db.get_recent_logs(limit=15)

    lines = [f"📊 <b>Статистика</b>\n\nВсего пользователей: <b>{total}</b>\n\n<b>Последние действия:</b>"]
    for log in logs:
        dt = datetime.fromtimestamp(log["created_at"]).strftime("%d.%m %H:%M")
        lines.append(f"• {dt} — {log['user_id']} — {log['action']} {log['details'] or ''}")

    await callback.message.edit_text("\n".join(lines), reply_markup=kb.admin_menu_kb())
    await callback.answer()
