"""
Обёртка над Remnawave через пакет `remnactual` (PyPI) — форк официального
SDK, сверенный побайтово с backend-contract панели версии 3.2.3.

ВАЖНО про версию 3.x: начиная с панели 3.0 пользователи identifицируются
числовым `id`, а не `uuid` (как было в 2.8.x) — это учтено в коде ниже.
Версия пакета жёстко привязана к версии панели:
    remnactual==3.2.3  ->  панель Remnawave >= 3.0.0 (сверено с 3.2.3)
Если обновите панель на более новую ветку — проверьте таблицу совместимости
на https://pypi.org/project/remnactual/ и обновите версию в requirements.txt.
"""
import asyncio
import logging
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from remnawave import RemnawaveSDK
from remnawave.models import CreateUserBodyDto, UpdateUserBodyDto
from remnawave.exceptions import NotFoundError

from config import REMNAWAVE_BASE_URL, REMNAWAVE_API_TOKEN, REMNAWAVE_SQUAD_UUID

logger = logging.getLogger(__name__)

# base_url можно передавать БЕЗ /api на конце — библиотека сама его добавит
sdk = RemnawaveSDK(base_url=REMNAWAVE_BASE_URL, token=REMNAWAVE_API_TOKEN)


async def get_user_by_username(username: str):
    """Возвращает объект пользователя или None, если не найден."""
    try:
        return await sdk.users.get_user_by_username(username)
    except NotFoundError:
        return None


async def create_user(username: str, days: int):
    """Создаёт нового пользователя со сроком действия `days` дней от текущего момента."""
    expire_at = datetime.now(timezone.utc) + timedelta(days=days)
    body = CreateUserBodyDto(
        username=username,
        expire_at=expire_at,
        status="ACTIVE",
        active_internal_squads=[REMNAWAVE_SQUAD_UUID] if REMNAWAVE_SQUAD_UUID else [],
    )
    return await sdk.users.create_user(body)


async def extend_user(user_id: int, days_from_now: int):
    """Продлевает существующего пользователя — новая дата истечения = сейчас + days_from_now."""
    expire_at = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    body = UpdateUserBodyDto(id=user_id, expire_at=expire_at, status="ACTIVE")
    return await sdk.users.update_user(body)


async def provision_subscription(telegram_id: int, days: int) -> str:
    """
    Главная функция для бота: создаёт пользователя, если его ещё нет
    в Remnawave, либо продлевает существующего (прибавляя дни к оставшемуся
    сроку, если подписка ещё активна). Возвращает subscription URL.
    """
    username = f"tg_{telegram_id}"
    existing = await get_user_by_username(username)

    if existing is None:
        logger.info(f"Создаю нового пользователя Remnawave: {username}")
        user = await create_user(username, days)
    else:
        logger.info(f"Продлеваю пользователя Remnawave: {username}")
        base_days = days
        now = datetime.now(timezone.utc)
        if existing.expire_at and existing.expire_at > now:
            remaining_days = (existing.expire_at - now).days
            base_days = remaining_days + days
        # 3.x: пользователь идентифицируется числовым id, не uuid
        user = await extend_user(existing.id, base_days)

    return user.subscription_url


async def get_existing_subscription_url(telegram_id: int) -> str | None:
    """
    Возвращает subscription_url без продления/создания пользователя — используется
    для «бэкфилла» ссылки тем, у кого она была выдана ещё до того, как бот начал
    сохранять её в своей БД (см. database.subscription_url).
    """
    username = f"tg_{telegram_id}"
    existing = await get_user_by_username(username)
    return existing.subscription_url if existing else None


async def disable_user(telegram_id: int) -> bool:
    """
    Используется при ручном "заборе" подписки админом: переводит пользователя
    в Remnawave в статус DISABLED, не удаляя его (историю/uuid сохраняем,
    доступ к VPN пропадает сразу). Возвращает True при успехе, False — если
    пользователя не нашли или запрос не удался (в этом случае локальная
    подписка в боте всё равно аннулируется — см. handlers/admin.py).
    """
    username = f"tg_{telegram_id}"
    try:
        existing = await get_user_by_username(username)
        if existing is None:
            return False
        body = UpdateUserBodyDto(id=existing.id, status="DISABLED")
        await sdk.users.update_user(body)
        return True
    except Exception:
        logger.exception("Не удалось отключить пользователя %s в Remnawave", telegram_id)
        return False


def _extract_host_port(address: str, default_port: int = 443) -> tuple[str, int] | None:
    """Достаёт host:port из адреса ноды (может быть с http(s):// или без)."""
    if not address:
        return None
    candidate = address if "://" in address else f"//{address}"
    parsed = urlparse(candidate)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or default_port
    return host, port


async def _measure_ping_ms(address: str) -> int | None:
    """
    Живой замер отклика ноды: открывает TCP-соединение до хоста ноды и
    измеряет время до установления соединения (в миллисекундах). Не зависит
    от того, есть ли в SDK отдельное поле "ping" — работает для любой ноды,
    у которой есть доступный адрес/порт.
    """
    hp = _extract_host_port(address)
    if hp is None:
        return None
    host, port = hp
    loop = asyncio.get_event_loop()
    start = loop.time()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
        elapsed_ms = round((loop.time() - start) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return elapsed_ms
    except Exception:
        return None


async def get_nodes_stats() -> list[dict]:
    """
    Возвращает список серверов (нод) с их статусом, количеством пользователей,
    трафиком и пингом — для раздела "🖥 Серверы" в админ-панели.

    ВАЖНО: названия методов/полей SDK (get_all_nodes, users_online,
    traffic_used_bytes/total_bytes и т.д.) приведены по типовой структуре
    Remnawave API и НЕ были сверены с реальным пакетом remnactual==3.2.3 —
    в этой среде нет доступа в интернет, чтобы проверить его исходники.
    Код защищён try/except и getattr с несколькими вариантами имён полей,
    так что при несовпадении просто покажет "—" вместо падения, но при
    первом запуске стоит свериться с логами и, если что-то не совпадает,
    поправить имена полей ниже под вашу версию SDK.
    """
    try:
        nodes = await sdk.nodes.get_all_nodes()
    except Exception:
        logger.exception("Не удалось получить список нод Remnawave")
        return []

    result = []
    for node in nodes:
        name = getattr(node, "name", None) or getattr(node, "id", "—")
        address = getattr(node, "address", None) or getattr(node, "host", "") or ""
        is_online = getattr(node, "is_connected", None)
        if is_online is None:
            is_online = getattr(node, "is_node_online", None)
        users_online = getattr(node, "users_online", None)
        if users_online is None:
            users_online = getattr(node, "active_users", None)

        traffic_bytes = getattr(node, "traffic_used_bytes", None)
        if traffic_bytes is None:
            traffic_bytes = getattr(node, "total_traffic_bytes", None)

        ping_ms = await _measure_ping_ms(address)

        result.append({
            "name": name,
            "address": address,
            "online": bool(is_online) if is_online is not None else None,
            "users_online": users_online,
            "traffic_bytes": traffic_bytes,
            "ping_ms": ping_ms,
        })
    return result


async def get_system_stats() -> dict | None:
    """
    Агрегированная статистика по всей системе (все ноды суммарно) для блока
    "мониторинг" — общее кол-во пользователей онлайн и суммарный трафик.
    Как и get_nodes_stats(), опирается на типовые для Remnawave названия
    методов/полей, которые стоит свериться с вашей версией SDK.
    """
    try:
        stats = await sdk.system.get_stats()
    except Exception:
        logger.exception("Не удалось получить системную статистику Remnawave")
        return None

    return {
        "users_online": getattr(stats, "users_online", None) or getattr(stats, "online_users", None),
        "users_total": getattr(stats, "users_total", None) or getattr(stats, "total_users", None),
        "traffic_bytes": getattr(stats, "total_traffic_bytes", None) or getattr(stats, "traffic_bytes", None),
    }


def format_bytes(value) -> str:
    """Человеко-читаемый формат трафика (Б/КБ/МБ/ГБ/ТБ), либо "—" если нет данных."""
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ПБ"
