"""
Раздел "Инструкция": как скачать приложение Happ и настроить фрагментацию
трафика — отдельно для телефона и для компьютера.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery

import keyboards as kb
from config import HAPP_DOWNLOAD_URL

router = Router()

INTRO_TEXT = (
    "📖 <b>Инструкция по подключению</b>\n\n"
    "Мы рекомендуем приложение <b>Happ</b> — оно работает и на телефоне, "
    "и на компьютере, а фрагментация трафика в нём помогает подключаться "
    "стабильнее там, где обычный VPN подключается плохо или медленно.\n\n"
    "Выбери своё устройство:"
)

PHONE_TEXT = (
    "📱 <b>Инструкция для телефона (iOS / Android)</b>\n\n"
    f"1️⃣ Скачай приложение Happ: {HAPP_DOWNLOAD_URL}\n"
    "2️⃣ Открой раздел «🔑 Подписка» в этом боте и перейди по своей ссылке "
    "подключения — Happ добавит её автоматически (либо добавь вручную "
    "через «+» в приложении).\n"
    "3️⃣ Зайди в настройки подключения → раздел <b>«Туннель»</b> и включи "
    "фрагментирование со следующими значениями:\n\n"
    "• Использовать фрагментирование: <b>Включено</b>\n"
    "• Тип: <b>Xray</b>\n"
    "• Кол-во пакетов: <b>tlshello</b>\n"
    "• Длина фрагмента (мин.–макс.): <b>15–25</b>\n"
    "• Задержка фр-ции (мин.–макс.): <b>10–20</b>\n"
    "• Макс. кол-во фрагментов: <b>3–6</b>\n\n"
    "Также предпочитаемый тип IP ставим IPv6.\n\n"
    "4️⃣ Сохрани настройки и подключайся.\n\n"
    "💡 Эти значения проверены и стабильно работают. Если соединение всё "
    "равно нестабильно — попробуй немного увеличить диапазоны или напиши в поддержку."
)

PC_TEXT = (
    "💻 <b>Инструкция для компьютера (Windows / macOS / Linux)</b>\n\n"
    f"1️⃣ Скачай приложение Happ: {HAPP_DOWNLOAD_URL}\n"
    "2️⃣ Открой раздел «🔑 Подписка» в этом боте, скопируй свою ссылку "
    "подключения и добавь её в приложении (кнопка добавления подписки).\n"
    "3️⃣ Открой <b>«Настройки туннеля»</b> и включи фрагментацию со "
    "следующими значениями:\n\n"
    "• Включить фрагментацию: <b>Вкл.</b>\n"
    "• Тип: <b>Xray</b>\n"
    "• Пакеты фрагментации: <b>tlshello</b>\n"
    "• Длина фрагмента (мин-макс): <b>10–20</b>\n"
    "• Задержка фрагментации (мин-макс): <b>5–10</b>\n"
    "• Максимальное разделение фрагмента (мин-макс): <b>3–5</b>\n"
    "• Включить шумы: можно оставить <b>выключенным</b>\n\n"
    "4️⃣ Сохрани настройки и подключайся.\n\n"
    "💡 Эти значения проверены и стабильно работают. Если соединение всё "
    "равно нестабильно — попробуй немного увеличить диапазоны или напиши в поддержку."
)


@router.callback_query(F.data == "menu_instructions")
async def cb_instructions_menu(callback: CallbackQuery):
    await callback.message.edit_text(INTRO_TEXT, reply_markup=kb.instructions_platform_kb())
    await callback.answer()


@router.callback_query(F.data == "instructions_phone")
async def cb_instructions_phone(callback: CallbackQuery):
    await callback.message.edit_text(PHONE_TEXT, reply_markup=kb.instructions_back_kb())
    await callback.answer()


@router.callback_query(F.data == "instructions_pc")
async def cb_instructions_pc(callback: CallbackQuery):
    await callback.message.edit_text(PC_TEXT, reply_markup=kb.instructions_back_kb())
    await callback.answer()
