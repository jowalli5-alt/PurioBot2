from aiogram.fsm.state import State, StatesGroup


class AdminGiveBalance(StatesGroup):
    waiting_user_id = State()
    waiting_amount = State()


class TopUpBalance(StatesGroup):
    """Пользователь вводит сумму пополнения баланса вручную."""
    waiting_amount = State()


class AdminGiveSubscription(StatesGroup):
    """Админ выдаёт подписку пользователю вручную (по ID и кол-ву дней)."""
    waiting_user_id = State()
    waiting_days = State()


class AdminRevokeSubscription(StatesGroup):
    """Админ забирает (аннулирует) подписку пользователя вручную."""
    waiting_user_id = State()


class AdminBroadcast(StatesGroup):
    waiting_text = State()


class AdminMessageUser(StatesGroup):
    """Отправка личного сообщения одному пользователю по Telegram ID."""
    waiting_user_id = State()
    waiting_text = State()


class AdminFindUser(StatesGroup):
    """Поиск карточки пользователя (баланс, подписка, рефералы) по Telegram ID."""
    waiting_user_id = State()


class TicketCreate(StatesGroup):
    """Пользователь создаёт тикет в поддержку."""
    waiting_text = State()


class AdminTicketReply(StatesGroup):
    """Админ отвечает на тикет."""
    waiting_text = State()


class PromoActivate(StatesGroup):
    """Пользователь вводит промокод."""
    waiting_code = State()


class AdminCreatePromo(StatesGroup):
    """Админ создаёт промокод: сам придумывает текст кода, задаёт кол-во
    дней подписки и лимит активаций."""
    waiting_code = State()
    waiting_days = State()
    waiting_activations = State()
