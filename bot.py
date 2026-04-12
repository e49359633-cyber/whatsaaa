import os
import logging
import json
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8756393401:AAGq-Nki_ZGXVAjeE7CrYChxdFaP7O3RGAU")
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@morphine_lz")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "-1003696334786")  # ID группы для команды /number
DATA_FILE = "bot_data.json"
LOG_FILE = "stand_log.txt"
STOOD_LOG_FILE = "stood_log.txt"    # Отстояли
FAILED_LOG_FILE = "failed_log.txt"  # Не отстояли (слет)
TARIFF_STAND_MINUTES = {
    "KZ 5/15": 15,
    "KZ 8/25": 25,
    "KZ 10/60": 60,
}
STAND_TIME_MINUTES = 25  # По умолчанию
ADMIN_USERNAMES = [x.strip().lower().replace("@", "") for x in os.getenv("ADMIN_USERNAMES", "morphine_lz,Bombai999,ketshon").split(",") if x.strip()]

# Кастомные эмодзи — статусы
E_OK = '<tg-emoji emoji-id="5206607081334906820">✅</tg-emoji>'       # галочка — встал/отстоял
E_SLET = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'     # крестик — слет
E_ERROR = '<tg-emoji emoji-id="5447644880824181073">❌</tg-emoji>'    # ошибка
E_RETRY = '<tg-emoji emoji-id="5449683594425410231">🔄</tg-emoji>'   # повтор
E_SKIP = '<tg-emoji emoji-id="5210956306952758910">⏭</tg-emoji>'     # скип
# Кастомные эмодзи — меню
E_SUBMIT = '<tg-emoji emoji-id="5397916757333654639">📱</tg-emoji>'   # сдать номер
E_ARCHIVE = '<tg-emoji emoji-id="5456140674028019486">📂</tg-emoji>'  # архив
E_QUEUE = '<tg-emoji emoji-id="5244837092042750681">📋</tg-emoji>'    # очередь
E_SUPPORT = '<tg-emoji emoji-id="5296369303661067030">👨‍💻</tg-emoji>'  # тех поддержка
E_LUNCH = '<tg-emoji emoji-id="5447410659077661506">🍽</tg-emoji>'    # обеды

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# FSM СОСТОЯНИЯ
# ============================================================================

class PhoneSubmissionStates(StatesGroup):
    waiting_for_phone = State()

class AdminStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_broadcast = State()
    waiting_for_sms = State()
    waiting_for_admin_id = State()
    waiting_for_group_id = State()

class ReviewStates(StatesGroup):
    waiting_for_review = State()

# ============================================================================
# РАБОТА С JSON ФАЙЛОМ
# ============================================================================

def load_data() -> dict:
    """Загрузка данных из JSON файла"""
    if not os.path.exists(DATA_FILE):
        return {"submissions": [], "reviews": [], "admins": []}
    
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при загрузке данных: {e}")
        return {"submissions": [], "reviews": [], "admins": []}

def save_data(data: dict) -> bool:
    """Сохранение данных в JSON файл"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных: {e}")
        return False

def init_data():
    """Инициализация JSON файла при старте бота"""
    if not os.path.exists(DATA_FILE):
        initial_groups = [GROUP_CHAT_ID] if GROUP_CHAT_ID else []
        save_data({"submissions": [], "reviews": [], "admins": [], "settings": {"group_ids": initial_groups}})
        logger.info("JSON файл инициализирован")
    else:
        data = load_data()
        changed = False
        if "admins" not in data:
            data["admins"] = []
            changed = True
        if "settings" not in data:
            data["settings"] = {"group_ids": [GROUP_CHAT_ID] if GROUP_CHAT_ID else []}
            changed = True
        # Миграция: одна группа → список групп
        settings = data.get("settings", {})
        if "group_ids" not in settings:
            old_id = settings.get("group_chat_id", GROUP_CHAT_ID)
            settings["group_ids"] = [old_id] if old_id else []
            if "group_chat_id" in settings:
                del settings["group_chat_id"]
            data["settings"] = settings
            changed = True
        if changed:
            save_data(data)
        logger.info("JSON файл уже существует")

def get_group_ids() -> list:
    """Получить список ID групп из JSON"""
    data = load_data()
    return data.get("settings", {}).get("group_ids", [])

def is_allowed_group(chat_id: int) -> bool:
    """Проверить, разрешена ли группа"""
    group_ids = get_group_ids()
    if not group_ids:
        return True
    return str(chat_id) in group_ids

def add_group_id(new_id: str) -> bool:
    """Добавить ID группы (макс 10)"""
    data = load_data()
    if "settings" not in data:
        data["settings"] = {"group_ids": []}
    group_ids = data["settings"].get("group_ids", [])
    if new_id in group_ids:
        return False
    if len(group_ids) >= 10:
        return False
    group_ids.append(new_id)
    data["settings"]["group_ids"] = group_ids
    save_data(data)
    return True

def remove_group_id(gid: str) -> bool:
    """Удалить ID группы"""
    data = load_data()
    group_ids = data.get("settings", {}).get("group_ids", [])
    if gid in group_ids:
        group_ids.remove(gid)
        data["settings"]["group_ids"] = group_ids
        save_data(data)
        return True
    return False

def is_super_admin(user_id: int = None, username: str = None) -> bool:
    """Проверка, является ли пользователь суперадмином (указан в коде)"""
    if username:
        return username.lower().replace("@", "") in ADMIN_USERNAMES
    return False

def is_bot_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом бота (выдан через /giveadmin)"""
    data = load_data()
    admins = data.get("admins", [])
    return any(a["user_id"] == user_id for a in admins)

def add_bot_admin(user_id: int, username: str) -> bool:
    """Добавить пользователя как админа бота"""
    data = load_data()
    if "admins" not in data:
        data["admins"] = []
    if any(a["user_id"] == user_id for a in data["admins"]):
        return False
    data["admins"].append({
        "user_id": user_id,
        "username": username,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    save_data(data)
    return True

def remove_bot_admin(user_id: int) -> bool:
    """Убрать пользователя из админов бота"""
    data = load_data()
    admins = data.get("admins", [])
    for a in admins:
        if a["user_id"] == user_id:
            admins.remove(a)
            save_data(data)
            return True
    return False

def save_phone_submission(user_id: int, phone_number: str, tariff: str) -> bool:
    """Сохранение номера телефона в JSON файл"""
    try:
        data = load_data()
        
        submission = {
            "id": len(data["submissions"]) + 1,
            "user_id": user_id,
            "phone_number": phone_number,
            "tariff": tariff,
            "status": "pending",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        data["submissions"].append(submission)
        
        if save_data(data):
            logger.info(f"Номер {phone_number} сохранен для пользователя {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка при сохранении номера: {e}")
        return False

def get_user_submissions(user_id: int) -> list:
    """Получение всех номеров пользователя из JSON файла"""
    try:
        data = load_data()
        user_submissions = []
        for sub in data["submissions"]:
            if sub["user_id"] == user_id:
                user_submissions.append(sub)
        return sorted(user_submissions, key=lambda x: x["created_at"], reverse=True)
    except Exception as e:
        logger.error(f"Ошибка при получении номеров: {e}")
        return []

def get_queue_position(user_id: int) -> tuple:
    """Получение позиции пользователя в очереди"""
    try:
        data = load_data()
        pending_submissions = [
            sub for sub in data["submissions"]
            if sub["status"] == "pending"
        ]
        
        pending_submissions.sort(key=lambda x: x["created_at"])
        
        user_positions = []
        for idx, sub in enumerate(pending_submissions, 1):
            if sub["user_id"] == user_id:
                user_positions.append((idx, sub["phone_number"], sub["id"]))
        
        total_pending = len(pending_submissions)
        return user_positions, total_pending
    except Exception as e:
        logger.error(f"Ошибка при получении очереди: {e}")
        return [], 0

def validate_kz_phone(phone: str) -> tuple:
    """Валидация номера телефона Казахстана
    
    Возвращает: (is_valid, cleaned_phone, error_message)
    """
    # Удаляем все пробелы, дефисы, скобки
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    
    # Варианты форматов казахстанских номеров:
    # +77XXXXXXXXX (12 символов с +)
    # 87XXXXXXXXX (11 символов)
    # 77XXXXXXXXX (11 символов)
    # 7XXXXXXXXX (10 символов)
    
    # Паттерн для казахстанских номеров
    patterns = [
        r'^\+7(7\d{9})$',      # +77XXXXXXXXX
        r'^8(7\d{9})$',        # 87XXXXXXXXX
        r'^(7\d{9})$',         # 7XXXXXXXXX
    ]
    
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if match:
            # Нормализуем к формату +77XXXXXXXXX
            normalized = '+7' + match.group(1)
            return True, normalized, None
    
    # Если не подошел ни один паттерн
    error_msg = (
        "❌ Неверный формат номера!\n\n"
        "Принимаются только казахстанские номера в формате:\n"
        "• +77XXXXXXXXX\n"
        "• 87XXXXXXXXX\n"
        "• 77XXXXXXXXX\n\n"
        "Пример: +77001234567 или 87001234567"
    )
    return False, None, error_msg

def get_next_number_from_queue() -> Optional[dict]:
    """Получение следующего номера из очереди и обновление его статуса"""
    try:
        data = load_data()
        pending_submissions = [
            sub for sub in data["submissions"]
            if sub["status"] == "pending"
        ]
        
        if not pending_submissions:
            return None
        
        # Сортируем по дате создания (самый старый первым)
        pending_submissions.sort(key=lambda x: x["created_at"])
        next_submission = pending_submissions[0]
        
        # Обновляем статус на "processing"
        for sub in data["submissions"]:
            if sub["id"] == next_submission["id"]:
                sub["status"] = "processing"
                sub["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break
        
        save_data(data)
        return next_submission
    except Exception as e:
        logger.error(f"Ошибка при получении номера из очереди: {e}")
        return None

# ============================================================================
# КЛАВИАТУРЫ
# ============================================================================

def get_main_menu_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    """Главное меню с инлайн-кнопками и счетчиками"""
    data = load_data()
    
    # Считаем очередь (pending)
    queue_count = len([s for s in data["submissions"] if s["status"] == "pending"])
    
    # Считаем архив пользователя
    archive_count = 0
    if user_id:
        archive_count = len([s for s in data["submissions"] if s["user_id"] == user_id])
    
    keyboard = [
        [
            InlineKeyboardButton(text=f"📱 Сдать номер", callback_data="menu_submit"),
            InlineKeyboardButton(text=f"📋 Очередь ({queue_count})", callback_data="menu_queue"),
        ],
        [
            InlineKeyboardButton(text=f"📂 Архив ({archive_count})", callback_data="menu_archive"),
            InlineKeyboardButton(text=f"👨‍💻 Тех поддержка", callback_data="menu_support"),
        ],
        [
            InlineKeyboardButton(text=f"🍽 Обеды", callback_data="menu_lunch"),
            InlineKeyboardButton(text=f"⭐ Отзывы", callback_data="menu_reviews"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_tariff_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с выбором тарифов"""
    keyboard = [
        [InlineKeyboardButton(text="Тариф KZ 5/15", callback_data="tariff_5_15")],
        [InlineKeyboardButton(text="Тариф KZ 8/25", callback_data="tariff_8_25")],
        [InlineKeyboardButton(text="Тариф KZ 10/60", callback_data="tariff_10_60")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ============================================================================
# РОУТЕРЫ И ХЭНДЛЕРЫ
# ============================================================================

router = Router()

# ----------------------------------------------------------------------------
# Команда /start
# ----------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    welcome_text = (
        f"{E_OK} Добро пожаловать!\n\n"
        "https://t.me/+mssqNjFVE_E5OGUy\n"
        f"{E_OK} Выберите действие:"
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_menu_keyboard(message.from_user.id),
        parse_mode="HTML"
    )

# ----------------------------------------------------------------------------
# Обработчики инлайн-меню
# ----------------------------------------------------------------------------

@router.callback_query(F.data == "menu_submit")
async def menu_submit_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Сдать номер'"""
    await callback.message.edit_text(
        f"{E_SUBMIT} Выберите тариф:",
        reply_markup=get_tariff_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "menu_queue")
async def menu_queue_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Очередь'"""
    user_positions, total_pending = get_queue_position(callback.from_user.id)
    
    if not user_positions:
        text = (
            f"{E_QUEUE} У вас нет номеров в очереди.\n"
            f"Всего номеров в очереди: {total_pending}"
        )
        buttons = []
    else:
        text = f"{E_QUEUE} Ваши позиции в очереди:\n\n"
        buttons = []
        for position, phone, sub_id in user_positions:
            text += f"📱 {phone}\n"
            text += f"⏳ Позиция: {position} из {total_pending}\n\n"
            buttons.append([InlineKeyboardButton(
                text=f"🗑 Удалить {phone}",
                callback_data=f"qdel_{sub_id}"
            )])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_archive")
async def menu_archive_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Архив'"""
    submissions = get_user_submissions(callback.from_user.id)
    
    if not submissions:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
        ])
        await callback.message.edit_text(
            f"{E_ARCHIVE} Ваш архив пуст.\nВы еще не сдавали номера.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отстоял", callback_data="arch_done"),
            InlineKeyboardButton(text="❌ Не отстоял", callback_data="arch_slet"),
        ],
        [
            InlineKeyboardButton(text="⏳ В очереди", callback_data="arch_pending"),
            InlineKeyboardButton(text="📊 Все", callback_data="arch_all"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ])
    
    archive_text = format_archive(submissions, f"{E_ARCHIVE} Ваш архив номеров:")
    await callback.message.edit_text(archive_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "menu_support")
async def menu_support_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Тех поддержка'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ])
    await callback.message.edit_text(
        f"{E_SUPPORT} Техническая поддержка\n\n"
        f"Если у вас возникли вопросы или проблемы,\n"
        f"обратитесь к администратору: {ADMIN_CONTACT}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "menu_lunch")
async def menu_lunch_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Обеды'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ])
    await callback.message.edit_text(
        f"{E_LUNCH} Обеды 14:00-14:30 по времени КЗ.\n"
        f"В это время выплаты и проверки могут задерживаться.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "menu_reviews")
async def menu_reviews_handler(callback: CallbackQuery):
    """Инлайн-кнопка 'Отзывы'"""
    data = load_data()
    reviews = data.get("reviews", [])
    
    # Показываем последние 5 отзывов
    if not reviews:
        text = "⭐ Отзывов пока нет.\nБудьте первым!"
    else:
        text = "⭐ Последние отзывы:\n\n"
        for r in reviews[-5:]:
            text += f"👤 {r.get('username', 'Аноним')}\n"
            text += f"📅 {r['date']}\n"
            text += f"💬 {r['text']}\n\n"
    
    # Проверяем, писал ли уже сегодня
    today = datetime.now().strftime("%Y-%m-%d")
    user_id = callback.from_user.id
    wrote_today = any(
        r["user_id"] == user_id and r["date"] == today
        for r in reviews
    )
    
    buttons = []
    if not wrote_today:
        buttons.append([InlineKeyboardButton(text="✍️ Написать отзыв", callback_data="review_write")])
    else:
        text += "ℹ️ Вы уже оставили отзыв сегодня."
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "review_write")
async def review_write_handler(callback: CallbackQuery, state: FSMContext):
    """Начало написания отзыва"""
    await state.set_state(ReviewStates.waiting_for_review)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="review_cancel")]
    ])
    await callback.message.edit_text(
        "✍️ Напишите ваш отзыв:\n\n"
        "Отправьте текст сообщением.",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data == "review_cancel")
async def review_cancel_handler(callback: CallbackQuery, state: FSMContext):
    """Отмена написания отзыва"""
    await state.clear()
    # Возвращаем в меню отзывов
    data = load_data()
    reviews = data.get("reviews", [])
    
    if not reviews:
        text = "⭐ Отзывов пока нет.\nБудьте первым!"
    else:
        text = "⭐ Последние отзывы:\n\n"
        for r in reviews[-5:]:
            text += f"👤 {r.get('username', 'Аноним')}\n"
            text += f"📅 {r['date']}\n"
            text += f"💬 {r['text']}\n\n"
    
    today = datetime.now().strftime("%Y-%m-%d")
    user_id = callback.from_user.id
    wrote_today = any(
        r["user_id"] == user_id and r["date"] == today
        for r in reviews
    )
    
    buttons = []
    if not wrote_today:
        buttons.append([InlineKeyboardButton(text="✍️ Написать отзыв", callback_data="review_write")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.message(StateFilter(ReviewStates.waiting_for_review))
async def review_text_received(message: Message, state: FSMContext):
    """Получение текста отзыва и сохранение"""
    data = load_data()
    if "reviews" not in data:
        data["reviews"] = []
    
    today = datetime.now().strftime("%Y-%m-%d")
    user_id = message.from_user.id
    
    # Проверка: 1 отзыв в день
    wrote_today = any(
        r["user_id"] == user_id and r["date"] == today
        for r in data["reviews"]
    )
    
    if wrote_today:
        await message.answer(
            "ℹ️ Вы уже оставили отзыв сегодня. Попробуйте завтра!",
            reply_markup=get_main_menu_keyboard(user_id),
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    username = message.from_user.first_name or "Аноним"
    
    review = {
        "user_id": user_id,
        "username": username,
        "text": message.text.strip()[:500],
        "date": today,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    data["reviews"].append(review)
    save_data(data)
    
    await message.answer(
        "⭐ Спасибо за ваш отзыв!",
        reply_markup=get_main_menu_keyboard(user_id),
        parse_mode="HTML"
    )
    await state.clear()

@router.callback_query(F.data == "menu_back")
async def menu_back_handler(callback: CallbackQuery):
    """Кнопка 'Назад' — возврат в главное меню"""
    welcome_text = (
        f"{E_OK} Добро пожаловать!\n\n"
        f"{E_OK} Выберите действие:"
    )
    await callback.message.edit_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(callback.from_user.id),
        parse_mode="HTML"
    )
    await callback.answer()

# ----------------------------------------------------------------------------
# Удаление номера из очереди
# ----------------------------------------------------------------------------

@router.callback_query(F.data.startswith("qdel_"))
async def queue_delete_handler(callback: CallbackQuery):
    """Удаление номера из очереди пользователем"""
    sub_id = int(callback.data.replace("qdel_", ""))
    
    data = load_data()
    deleted = False
    for s in data["submissions"]:
        if s["id"] == sub_id and s["user_id"] == callback.from_user.id and s["status"] == "pending":
            data["submissions"].remove(s)
            deleted = True
            phone = s["phone_number"]
            break
    
    if deleted:
        save_data(data)
        await callback.answer(f"✅ Номер {phone} удалён из очереди")
    else:
        await callback.answer("❌ Не удалось удалить номер")
    
    # Обновляем список очереди
    user_positions, total_pending = get_queue_position(callback.from_user.id)
    
    if not user_positions:
        text = (
            f"{E_QUEUE} У вас нет номеров в очереди.\n"
            f"Всего номеров в очереди: {total_pending}"
        )
        buttons = []
    else:
        text = f"{E_QUEUE} Ваши позиции в очереди:\n\n"
        buttons = []
        for position, phone, sid in user_positions:
            text += f"📱 {phone}\n"
            text += f"⏳ Позиция: {position} из {total_pending}\n\n"
            buttons.append([InlineKeyboardButton(
                text=f"🗑 Удалить {phone}",
                callback_data=f"qdel_{sid}"
            )])
    
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

# ----------------------------------------------------------------------------
# Кнопка "Сдать номер" (текстовая — обратная совместимость)
# ----------------------------------------------------------------------------

@router.message(F.text == "Сдать номер")
async def submit_phone_start(message: Message):
    """Обработчик кнопки 'Сдать номер'"""
    await message.answer(
        f"{E_SUBMIT} Выберите тариф:",
        reply_markup=get_tariff_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("tariff_"))
async def tariff_selected(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора тарифа"""
    tariff_code = callback.data.replace("tariff_", "")
    
    tariff_map = {
        "5_15": "KZ 5/15",
        "8_25": "KZ 8/25",
        "10_60": "KZ 10/60",
    }
    tariff_name = tariff_map.get(tariff_code, "Неизвестный")
    
    await state.update_data(selected_tariff=tariff_name)
    await state.set_state(PhoneSubmissionStates.waiting_for_phone)
    
    await callback.message.edit_text(
        f"✅ Выбран тариф: {tariff_name}\n\n"
        "📞 Теперь введите номер телефона:"
    )
    await callback.answer()

@router.message(StateFilter(PhoneSubmissionStates.waiting_for_phone))
async def phone_number_received(message: Message, state: FSMContext):
    """Обработчик ввода номера телефона"""
    phone_input = message.text.strip()
    
    # Валидация номера
    is_valid, phone_number, error_msg = validate_kz_phone(phone_input)
    
    if not is_valid:
        await message.answer(error_msg)
        return
    
    # Проверка на дубликат — номер уже в очереди
    data = load_data()
    for s in data["submissions"]:
        if s["phone_number"] == phone_number and s["status"] in ["pending", "code_sent", "standing"]:
            await message.answer(
                f"{E_ERROR} Номер {phone_number} уже в очереди!\n\n"
                f"📊 Статус: {STATUS_LABELS.get(s['status'], s['status'])}",
                reply_markup=get_main_menu_keyboard(message.from_user.id),
                parse_mode="HTML"
            )
            await state.clear()
            return
    
    user_data = await state.get_data()
    tariff = user_data.get("selected_tariff", "Неизвестный")
    
    success = save_phone_submission(
        user_id=message.from_user.id,
        phone_number=phone_number,
        tariff=tariff
    )
    
    if success:
        await message.answer(
            f"{E_OK} Номер успешно сохранен!\n\n"
            f"📱 Номер: {phone_number}\n"
            f"💳 Тариф: {tariff}\n"
            f"📊 Статус: В обработке",
            reply_markup=get_main_menu_keyboard(message.from_user.id),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"{E_ERROR} Произошла ошибка при сохранении номера. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(message.from_user.id),
            parse_mode="HTML"
        )
    
    await state.clear()

# ----------------------------------------------------------------------------
# Кнопка "Обеды"
# ----------------------------------------------------------------------------

@router.message(F.text == "Обеды")
async def lunch_info(message: Message):
    """Обработчик кнопки 'Обеды'"""
    await message.answer(
        f"{E_LUNCH} Обеды 14:00-14:30 по времени КЗ.\n"
        f"В это время выплаты и проверки могут задерживаться.",
        parse_mode="HTML"
    )

STATUS_LABELS = {
    "pending": "⏳ В очереди",
    "code_sent": "📨 Код отправлен",
    "standing": "⏱ Отстаивает...",
    "done": "✅ Отстоял",
    "slet": "❌ Слет",
    "skipped": "⏭ Пропущен",
    "error": "❌ Ошибка",
}

def format_archive(submissions: list, title: str) -> str:
    """Форматирование списка номеров для архива"""
    if not submissions:
        return f"{title}\n\nНет номеров."
    
    text = f"{title}\n\n"
    for idx, sub in enumerate(submissions, 1):
        status_text = STATUS_LABELS.get(sub["status"], sub["status"])
        stood_at = sub.get("stood_at", "")
        
        text += f"{idx}. 📱 {sub['phone_number']}\n"
        text += f"   💳 Тариф: {sub['tariff']}\n"
        
        if stood_at:
            text += f"   🕒 Встал: {stood_at}\n"
        
        text += f"   📊 Статус: {status_text}\n\n"
    return text

@router.message(F.text == "Архив")
async def archive_info(message: Message):
    """Обработчик кнопки 'Архив' — показывает кнопки-фильтры"""
    submissions = get_user_submissions(message.from_user.id)
    
    if not submissions:
        await message.answer(
            f"{E_ARCHIVE} Ваш архив пуст.\n"
            f"Вы еще не сдавали номера.",
            parse_mode="HTML"
        )
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отстоял", callback_data="arch_done"),
            InlineKeyboardButton(text="❌ Не отстоял", callback_data="arch_slet"),
        ],
        [
            InlineKeyboardButton(text="⏳ В очереди", callback_data="arch_pending"),
            InlineKeyboardButton(text="📊 Все", callback_data="arch_all"),
        ]
    ])
    
    archive_text = format_archive(submissions, f"{E_ARCHIVE} Ваш архив номеров:")
    await message.answer(archive_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("arch_"))
async def archive_filter_handler(callback: CallbackQuery):
    """Фильтрация архива по статусу"""
    filter_type = callback.data.replace("arch_", "")
    all_subs = get_user_submissions(callback.from_user.id)
    
    if filter_type == "done":
        filtered = [s for s in all_subs if s["status"] == "done"]
        title = "✅ Отстояли:"
    elif filter_type == "slet":
        filtered = [s for s in all_subs if s["status"] in ["slet", "error", "skipped"]]
        title = "❌ Не отстояли:"
    elif filter_type == "pending":
        filtered = [s for s in all_subs if s["status"] in ["pending", "code_sent", "standing"]]
        title = "⏳ В очереди / в работе:"
    else:
        filtered = all_subs
        title = f"{E_ARCHIVE} Ваш архив номеров:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отстоял", callback_data="arch_done"),
            InlineKeyboardButton(text="❌ Не отстоял", callback_data="arch_slet"),
        ],
        [
            InlineKeyboardButton(text="⏳ В очереди", callback_data="arch_pending"),
            InlineKeyboardButton(text="📊 Все", callback_data="arch_all"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ])
    
    archive_text = format_archive(filtered, title)
    await callback.message.edit_text(archive_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ----------------------------------------------------------------------------
# Кнопка "Очередь"
# ----------------------------------------------------------------------------

@router.message(F.text == "Очередь")
async def queue_info(message: Message):
    """Обработчик кнопки 'Очередь'"""
    user_positions, total_pending = get_queue_position(message.from_user.id)
    
    if not user_positions:
        await message.answer(
            f"{E_QUEUE} У вас нет номеров в очереди.\n"
            f"Всего номеров в очереди: {total_pending}",
            parse_mode="HTML"
        )
        return
    
    queue_text = f"{E_QUEUE} Ваши позиции в очереди:\n\n"
    buttons = []
    for position, phone, sub_id in user_positions:
        queue_text += f"📱 {phone}\n"
        queue_text += f"⏳ Позиция: {position} из {total_pending}\n\n"
        buttons.append([InlineKeyboardButton(
            text=f"🗑 Удалить {phone}",
            callback_data=f"qdel_{sub_id}"
        )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(queue_text, reply_markup=keyboard, parse_mode="HTML")

# ----------------------------------------------------------------------------
# Кнопка "Тех. поддержка"
# ----------------------------------------------------------------------------

@router.message(F.text == "Тех. поддержка")
async def support_info(message: Message):
    """Обработчик кнопки 'Тех. поддержка'"""
    await message.answer(
        f"{E_SUPPORT} Техническая поддержка\n\n"
        f"Если у вас возникли вопросы или проблемы,\n"
        f"обратитесь к администратору: {ADMIN_CONTACT}",
        parse_mode="HTML"
    )

# ----------------------------------------------------------------------------
# Админ-панель
# ----------------------------------------------------------------------------

def is_admin_by_username(username: str) -> bool:
    """Проверка, является ли пользователь админом по username"""
    if not username:
        return False
    return username.lower().replace("@", "") in ADMIN_USERNAMES

@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Админ-панель бота"""
    if message.chat.type != "private":
        return
    
    if not is_admin_by_username(message.from_user.username):
        await message.answer("⚠️ У вас нет доступа к админ-панели.")
        return
    
    data = load_data()
    pending = len([s for s in data["submissions"] if s["status"] == "pending"])
    standing = len([s for s in data["submissions"] if s["status"] == "standing"])
    done = len([s for s in data["submissions"] if s["status"] == "done"])
    slet = len([s for s in data["submissions"] if s["status"] == "slet"])
    admins = data.get("admins", [])
    group_ids = get_group_ids()
    
    groups_text = ""
    if group_ids:
        for i, gid in enumerate(group_ids, 1):
            groups_text += f"  {i}. <code>{gid}</code>\n"
    else:
        groups_text = "  Нет групп\n"
    
    text = (
        f"⚙️ Админ-панель\n\n"
        f"📋 В очереди: {pending}\n"
        f"⏱ Отстаивают: {standing}\n"
        f"✅ Отстояли: {done}\n"
        f"❌ Слетели: {slet}\n\n"
        f"🏠 Группы ({len(group_ids)}/10):\n{groups_text}\n"
        f"👥 Админов: {len(admins)}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Написать всем", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🗑 Очистить очередь", callback_data="adm_clear_queue")],
        [InlineKeyboardButton(text="📊 Скачать отчет", callback_data="adm_report")],
        [
            InlineKeyboardButton(text="➕ Добавить админа", callback_data="ap_add_admin"),
            InlineKeyboardButton(text="➖ Удалить админа", callback_data="ap_remove_admin"),
        ],
        [
            InlineKeyboardButton(text="➕ Добавить группу", callback_data="ap_add_group"),
            InlineKeyboardButton(text="➖ Удалить группу", callback_data="ap_remove_group"),
        ],
        [InlineKeyboardButton(text="📋 Список админов", callback_data="ap_list_admins")],
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "adm_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    """Начало рассылки — запрос текста"""
    if not is_admin_by_username(callback.from_user.username):
        await callback.answer("⚠️ Нет доступа")
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text(
        "📢 Введите сообщение для рассылки всем пользователям:\n\n"
        "(Отправьте /cancel для отмены)"
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_broadcast), Command("cancel"))
async def admin_broadcast_cancel(message: Message, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await message.answer("❌ Рассылка отменена.")

@router.message(StateFilter(AdminStates.waiting_for_broadcast))
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    """Отправка рассылки всем пользователям"""
    broadcast_text = message.text
    
    data = load_data()
    user_ids = set(sub["user_id"] for sub in data["submissions"])
    
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=f"📢 {broadcast_text}")
            sent += 1
        except Exception:
            failed += 1
    
    await state.clear()
    await message.answer(
        f"✅ Рассылка завершена!\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}"
    )

@router.callback_query(F.data == "adm_clear_queue")
async def admin_clear_queue(callback: CallbackQuery):
    """Очистка очереди (pending номеров)"""
    if not is_admin_by_username(callback.from_user.username):
        await callback.answer("⚠️ Нет доступа")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, очистить", callback_data="adm_clear_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="adm_clear_cancel")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите очистить всю очередь?\n"
        "Все номера со статусом 'В очереди' будут удалены.",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data == "adm_clear_confirm")
async def admin_clear_confirm(callback: CallbackQuery):
    """Подтверждение очистки очереди"""
    if not is_admin_by_username(callback.from_user.username):
        await callback.answer("⚠️ Нет доступа")
        return
    
    data = load_data()
    before = len(data["submissions"])
    data["submissions"] = [s for s in data["submissions"] if s["status"] != "pending"]
    removed = before - len(data["submissions"])
    save_data(data)
    
    await callback.message.edit_text(
        f"🗑 Очередь очищена!\n\n"
        f"Удалено номеров: {removed}"
    )
    await callback.answer()

@router.callback_query(F.data == "adm_clear_cancel")
async def admin_clear_cancel(callback: CallbackQuery):
    """Отмена очистки"""
    await callback.message.edit_text("❌ Очистка отменена.")
    await callback.answer()

@router.callback_query(F.data == "adm_report")
async def admin_report(callback: CallbackQuery, bot: Bot):
    """Скачать отчет — два TXT файла"""
    if not is_admin_by_username(callback.from_user.username):
        await callback.answer("⚠️ Нет доступа")
        return
    
    files_sent = 0
    
    # Отправляем файл отстоявших
    if os.path.exists(STOOD_LOG_FILE):
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=FSInputFile(STOOD_LOG_FILE),
            caption="✅ Отстояли"
        )
        files_sent += 1
    
    # Отправляем файл не отстоявших
    if os.path.exists(FAILED_LOG_FILE):
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=FSInputFile(FAILED_LOG_FILE),
            caption="❌ Не отстояли (слет)"
        )
        files_sent += 1
    
    if files_sent == 0:
        await callback.message.edit_text("📂 Отчеты пусты — пока нет данных.")
    else:
        await callback.message.edit_text(f"📊 Отправлено файлов: {files_sent}")
    await callback.answer()

# ----------------------------------------------------------------------------
# Команда /id для группы — узнать ID чата
# ----------------------------------------------------------------------------

@router.message(Command("id"))
async def get_chat_id(message: Message):
    """Показать ID группы"""
    if message.chat.type in ["group", "supergroup"]:
        await message.answer(f"🆔 ID этой группы: `{message.chat.id}`", parse_mode="Markdown")
    else:
        await message.answer(f"🆔 Ваш ID: `{message.from_user.id}`", parse_mode="Markdown")

@router.callback_query(F.data == "ap_add_admin")
async def ap_add_admin_handler(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Добавить админа' — запрос ID"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    await state.set_state(AdminStates.waiting_for_admin_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ap_cancel")]
    ])
    await callback.message.edit_text(
        "➕ Введите User ID пользователя для добавления в админы:\n\n"
        "Узнать ID можно через /id или @userinfobot",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data == "ap_remove_admin")
async def ap_remove_admin_handler(callback: CallbackQuery):
    """Кнопка 'Удалить админа' — показать список для удаления"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    data = load_data()
    admins = data.get("admins", [])
    
    if not admins:
        await callback.answer("Список админов пуст!")
        return
    
    buttons = []
    for a in admins:
        buttons.append([InlineKeyboardButton(
            text=f"❌ @{a['username']} (ID: {a['user_id']})",
            callback_data=f"ap_del_{a['user_id']}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ap_back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("➖ Выберите админа для удаления:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("ap_del_"))
async def ap_del_admin_handler(callback: CallbackQuery):
    """Удаление конкретного админа по кнопке"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    target_id = int(callback.data.replace("ap_del_", ""))
    if remove_bot_admin(target_id):
        await callback.answer("✅ Админ удалён!")
    else:
        await callback.answer("❌ Не найден")
    
    # Возвращаем в панель
    await _show_admin_panel(callback)

@router.callback_query(F.data == "ap_add_group")
async def ap_add_group_handler(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'Добавить группу'"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    group_ids = get_group_ids()
    if len(group_ids) >= 10:
        await callback.answer("⚠️ Максимум 10 групп!")
        return
    
    await state.set_state(AdminStates.waiting_for_group_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="ap_cancel")]
    ])
    await callback.message.edit_text(
        f"🏠 Текущие группы ({len(group_ids)}/10):\n\n"
        "Введите ID новой группы:\n"
        "Узнать ID можно добавив бота в группу и написав /id",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "ap_remove_group")
async def ap_remove_group_handler(callback: CallbackQuery):
    """Кнопка 'Удалить группу' — показать список для удаления"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    group_ids = get_group_ids()
    
    if not group_ids:
        await callback.answer("Список групп пуст!")
        return
    
    buttons = []
    for gid in group_ids:
        buttons.append([InlineKeyboardButton(
            text=f"❌ {gid}",
            callback_data=f"ap_delgrp_{gid}"
        )])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="ap_back")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("➖ Выберите группу для удаления:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("ap_delgrp_"))
async def ap_delgrp_handler(callback: CallbackQuery):
    """Удаление группы по кнопке"""
    username = callback.from_user.username or ""
    if not is_super_admin(username=username):
        await callback.answer("⚠️ Нет прав!")
        return
    
    gid = callback.data.replace("ap_delgrp_", "")
    if remove_group_id(gid):
        await callback.answer("✅ Группа удалена!")
    else:
        await callback.answer("❌ Не найдена")
    
    await _show_admin_panel(callback)

@router.callback_query(F.data == "ap_list_admins")
async def ap_list_admins_handler(callback: CallbackQuery):
    """Кнопка 'Список админов'"""
    data = load_data()
    admins = data.get("admins", [])
    
    if not admins:
        text = "📋 Список админов пуст."
    else:
        text = "📋 Список админов:\n\n"
        for a in admins:
            text += f"👤 @{a['username']} (ID: {a['user_id']})\n"
            text += f"📅 Добавлен: {a['added_at']}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="ap_back")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

async def _show_admin_panel(callback: CallbackQuery):
    """Общая функция отображения админ-панели"""
    data = load_data()
    pending = len([s for s in data["submissions"] if s["status"] == "pending"])
    standing = len([s for s in data["submissions"] if s["status"] == "standing"])
    done = len([s for s in data["submissions"] if s["status"] == "done"])
    slet = len([s for s in data["submissions"] if s["status"] == "slet"])
    admins = data.get("admins", [])
    group_ids = get_group_ids()
    
    groups_text = ""
    if group_ids:
        for i, gid in enumerate(group_ids, 1):
            groups_text += f"  {i}. <code>{gid}</code>\n"
    else:
        groups_text = "  Нет групп\n"
    
    text = (
        f"⚙️ Админ-панель\n\n"
        f"📋 В очереди: {pending}\n"
        f"⏱ Отстаивают: {standing}\n"
        f"✅ Отстояли: {done}\n"
        f"❌ Слетели: {slet}\n\n"
        f"🏠 Группы ({len(group_ids)}/10):\n{groups_text}\n"
        f"👥 Админов: {len(admins)}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Написать всем", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🗑 Очистить очередь", callback_data="adm_clear_queue")],
        [InlineKeyboardButton(text="📊 Скачать отчет", callback_data="adm_report")],
        [
            InlineKeyboardButton(text="➕ Добавить админа", callback_data="ap_add_admin"),
            InlineKeyboardButton(text="➖ Удалить админа", callback_data="ap_remove_admin"),
        ],
        [
            InlineKeyboardButton(text="➕ Добавить группу", callback_data="ap_add_group"),
            InlineKeyboardButton(text="➖ Удалить группу", callback_data="ap_remove_group"),
        ],
        [InlineKeyboardButton(text="📋 Список админов", callback_data="ap_list_admins")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "ap_back")
async def ap_back_handler(callback: CallbackQuery):
    """Назад в админ-панель"""
    await _show_admin_panel(callback)

@router.callback_query(F.data == "ap_cancel")
async def ap_cancel_handler(callback: CallbackQuery, state: FSMContext):
    """Отмена действия в админ-панели"""
    await state.clear()
    await _show_admin_panel(callback)

@router.message(StateFilter(AdminStates.waiting_for_admin_id))
async def admin_id_received(message: Message, state: FSMContext):
    """Получение User ID для добавления в админы"""
    text = message.text.strip()
    
    try:
        user_id = int(text)
    except ValueError:
        await message.answer("❌ Неверный формат! Введите числовой User ID:")
        return
    
    if is_bot_admin(user_id):
        await message.answer("ℹ️ Этот пользователь уже админ!")
        await state.clear()
        return
    
    add_bot_admin(user_id, str(user_id))
    await message.answer(
        f"✅ Админ добавлен!\n\n"
        f"👤 User ID: {user_id}\n\n"
        f"Откройте /admin для управления"
    )
    await state.clear()

@router.message(StateFilter(AdminStates.waiting_for_group_id))
async def group_id_received(message: Message, state: FSMContext):
    """Получение нового ID группы для добавления"""
    new_id = message.text.strip()
    
    if not new_id.startswith("-"):
        await message.answer("❌ ID группы должен начинаться с '-'\nНапример: -1001234567890")
        return
    
    if add_group_id(new_id):
        await message.answer(
            f"✅ Группа добавлена!\n\n"
            f"🏠 ID: <code>{new_id}</code>\n\n"
            f"Изменения применены сразу. /admin для управления.",
            parse_mode="HTML"
        )
    else:
        group_ids = get_group_ids()
        if new_id in group_ids:
            await message.answer("ℹ️ Эта группа уже добавлена!")
        else:
            await message.answer("⚠️ Максимум 10 групп! Удалите одну чтобы добавить новую.")
    await state.clear()

# ----------------------------------------------------------------------------
# Команда /giveadmin — выдать права админа
# ----------------------------------------------------------------------------

@router.message(Command("giveadmin"))
async def giveadmin_command(message: Message, bot: Bot):
    """Суперадмин выдаёт права админа пользователю: /giveadmin в ответ на сообщение"""
    # Только суперадмины
    username = message.from_user.username or ""
    if not is_super_admin(username=username):
        await message.answer("⚠️ Эта команда доступна только суперадминам!")
        return
    
    # Нужен реплай на сообщение пользователя
    if not message.reply_to_message:
        await message.answer(
            "⚠️ Ответьте на сообщение пользователя, которому хотите дать админку:\n\n"
            "Используйте реплай: /giveadmin"
        )
        return
    
    target = message.reply_to_message.from_user
    if target.is_bot:
        await message.answer("⚠️ Нельзя дать админку боту!")
        return
    
    target_username = target.username or "нет юзернейма"
    
    if is_bot_admin(target.id):
        await message.answer(f"ℹ️ @{target_username} уже является админом.")
        return
    
    add_bot_admin(target.id, target_username)
    await message.answer(
        f"✅ @{target_username} теперь админ!\n"
        f"👤 ID: {target.id}\n\n"
        f"Теперь может брать номера через /number"
    )

# ----------------------------------------------------------------------------
# Команда /removeadmin — убрать права админа
# ----------------------------------------------------------------------------

@router.message(Command("removeadmin"))
async def removeadmin_command(message: Message, bot: Bot):
    """Суперадмин убирает права админа: /removeadmin в ответ на сообщение"""
    username = message.from_user.username or ""
    if not is_super_admin(username=username):
        await message.answer("⚠️ Эта команда доступна только суперадминам!")
        return
    
    if not message.reply_to_message:
        await message.answer(
            "⚠️ Ответьте на сообщение пользователя:\n\n"
            "Используйте реплай: /removeadmin"
        )
        return
    
    target = message.reply_to_message.from_user
    target_username = target.username or "нет юзернейма"
    
    if remove_bot_admin(target.id):
        await message.answer(f"✅ @{target_username} больше не админ.")
    else:
        await message.answer(f"❌ @{target_username} не найден в списке админов.")

# ----------------------------------------------------------------------------
# Команда /admins — список админов
# ----------------------------------------------------------------------------

@router.message(Command("admins"))
async def admins_list_command(message: Message):
    """Показать список текущих админов бота"""
    username = message.from_user.username or ""
    if not is_super_admin(username=username):
        await message.answer("⚠️ Эта команда доступна только суперадминам!")
        return
    
    data = load_data()
    admins = data.get("admins", [])
    
    if not admins:
        await message.answer("📋 Список админов пуст.\n\nИспользуйте /giveadmin (реплай) чтобы добавить.")
        return
    
    text = "👑 Список админов:\n\n"
    for a in admins:
        text += f"👤 @{a['username']} (ID: {a['user_id']})\n"
        text += f"📅 Добавлен: {a['added_at']}\n\n"
    
    await message.answer(text)

# ----------------------------------------------------------------------------
# Команда /number для группы
# ----------------------------------------------------------------------------

@router.message(Command("number"))
async def get_number_command(message: Message, bot: Bot):
    """Обработчик команды /number для просмотра следующего номера из очереди в группе"""
    
    # Проверяем, что команда вызвана в группе
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("⚠️ Эта команда работает только в группах!")
        return
    
    # Проверяем, что это разрешённая группа
    if not is_allowed_group(message.chat.id):
        return
    
    # Проверяем, что пользователь — админ бота или суперадмин
    user_username = message.from_user.username or ""
    if not is_super_admin(username=user_username) and not is_bot_admin(message.from_user.id):
        await message.answer("⚠️ У вас нет прав! Попросите суперадмина выдать вам доступ через /giveadmin")
        return
    
    # Получаем следующий номер из очереди
    try:
        data = load_data()
        pending_submissions = [
            sub for sub in data["submissions"]
            if sub["status"] == "pending"
        ]
        
        if not pending_submissions:
            await message.answer(
                "📋 Очередь пуста!\n"
                "Нет номеров в ожидании обработки."
            )
            return
        
        # Сортируем по дате создания (самый старый первым)
        pending_submissions.sort(key=lambda x: x["created_at"])
        next_number = pending_submissions[0]
        sub_id = next_number["id"]
        
        # Формируем сообщение с информацией о номере
        response_text = (
            f"{E_QUEUE} Следующий номер из очереди:\n\n"
            f"🔢 Номер: <code>{next_number['phone_number']}</code>\n"
            f"💳 Тариф: {next_number['tariff']}\n"
            f"👤 User ID: <code>{next_number['user_id']}</code>\n"
            f"📅 Добавлен: {next_number['created_at']}"
        )
        
        # Инлайн-кнопки: Отправить код, Скип, Ошибка
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить код", callback_data=f"num_sendcode_{sub_id}"),
                InlineKeyboardButton(text="⏭ Скип", callback_data=f"num_skip_{sub_id}"),
                InlineKeyboardButton(text="❌ Ошибка", callback_data=f"num_error_{sub_id}")
            ]
        ])
        
        await message.answer(response_text, parse_mode="HTML", reply_markup=keyboard)
        logger.info(f"Номер {next_number['phone_number']} показан администратору {message.from_user.id}")
        
        # Уведомляем клиента, что его номер взяли
        try:
            await bot.send_message(
                chat_id=next_number["user_id"],
                text=(
                    f"{E_SUBMIT} Ваш номер взяли! Ожидайте код.\n\n"
                    f"📱 Номер: {next_number['phone_number']}"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления клиента о взятии номера: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка при получении номера: {e}")
        await message.answer("❌ Ошибка при получении номера из очереди")

# ----------------------------------------------------------------------------
# Обработчики кнопок для /number
# ----------------------------------------------------------------------------

def update_submission_status(sub_id: int, new_status: str) -> Optional[dict]:
    """Обновление статуса номера по ID"""
    try:
        data = load_data()
        for sub in data["submissions"]:
            if sub["id"] == sub_id:
                sub["status"] = new_status
                sub["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_data(data)
                return sub
        return None
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса: {e}")
        return None

@router.callback_query(F.data.startswith("num_sendcode_"))
async def num_sendcode_handler(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Отправить код' — просит отправить изображение"""
    sub_id = int(callback.data.replace("num_sendcode_", ""))
    
    # Сохраняем ID номера в состояние и ждем изображение
    await state.update_data(sub_id=sub_id)
    await state.set_state(AdminStates.waiting_for_image)
    
    await callback.message.edit_text(
        f"📷 Отправьте изображение (скриншот кода) для этого номера..."
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_image), F.photo)
async def admin_image_received(message: Message, state: FSMContext, bot: Bot):
    """Получение изображения от админа и отправка пользователю"""
    user_data = await state.get_data()
    sub_id = user_data.get("sub_id")
    
    sub = update_submission_status(sub_id, "code_sent")
    
    if sub:
        photo = message.photo[-1]
        try:
            await bot.send_photo(
                chat_id=sub["user_id"],
                photo=photo.file_id,
                caption=(
                    f"📱 Код для номера {sub['phone_number']}\n"
                    f"💳 Тариф: {sub['tariff']}"
                )
            )
            
            # Кнопки после отправки фото: Встал, Ошибка, Повтор, SMS
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Встал", callback_data=f"res_ok_{sub_id}"),
                    InlineKeyboardButton(text="❌ Ошибка", callback_data=f"res_err_{sub_id}"),
                ],
                [
                    InlineKeyboardButton(text="🔄 Повтор", callback_data=f"res_retry_{sub_id}"),
                    InlineKeyboardButton(text="💬 SMS", callback_data=f"res_sms_{sub_id}"),
                ]
            ])
            
            await message.reply(
                f"📷 Изображение отправлено пользователю!\n"
                f"🔢 Номер: {sub['phone_number']}\n\n"
                f"Выберите результат:",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Ошибка отправки изображения пользователю: {e}")
            await message.reply(
                f"❌ Не удалось отправить изображение пользователю.\n"
                f"Возможно, он не запустил бота."
            )
    else:
        await message.reply("❌ Ошибка: номер не найден")
    
    await state.clear()

@router.message(StateFilter(AdminStates.waiting_for_image))
async def admin_image_wrong_format(message: Message):
    """Если админ отправил не изображение"""
    await message.reply("⚠️ Пожалуйста, отправьте именно изображение (фото).")

# ----------------------------------------------------------------------------
# Кнопки результата после отправки кода (Встал / Ошибка / Повтор)
# ----------------------------------------------------------------------------

def write_stand_log(phone: str, stood_at_str: str, end_time: datetime, status: str):
    """Запись в лог-файлы: общий, отстояли, не отстояли"""
    try:
        stood_at = datetime.strptime(stood_at_str, "%Y-%m-%d %H:%M:%S")
        delta = end_time - stood_at
        total_seconds = delta.total_seconds()
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds % 1) * 1000)
        
        line = (
            f"{phone} | "
            f"Стоял: {minutes} мин {seconds} сек {milliseconds} мс | "
            f"Статус: {status} | "
            f"{end_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        # Общий лог
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
        
        # Раздельные логи
        if status == "отстоял":
            with open(STOOD_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line)
        else:
            with open(FAILED_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception as e:
        logger.error(f"Ошибка записи в лог: {e}")

async def auto_stand_check(sub_id: int, bot: Bot, stand_minutes: int):
    """Автоматическая проверка отстоя через N минут (зависит от тарифа)"""
    await asyncio.sleep(stand_minutes * 60)
    
    data = load_data()
    for sub in data["submissions"]:
        if sub["id"] == sub_id and sub["status"] == "standing":
            now = datetime.now()
            sub["status"] = "done"
            sub["done_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            save_data(data)
            
            write_stand_log(sub["phone_number"], sub["stood_at"], now, "отстоял")
            
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    chat_id=sub["user_id"],
                    text=(
                        f"{E_OK} Ваш номер отстоял {stand_minutes} минут!\n\n"
                        f"📱 Номер: {sub['phone_number']}\n"
                        f"💳 Тариф: {sub['tariff']}"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя об отстое: {e}")
            
            logger.info(f"Номер {sub['phone_number']} отстоял {stand_minutes} минут")
            break

@router.callback_query(F.data.startswith("res_ok_"))
async def result_ok_handler(callback: CallbackQuery, bot: Bot):
    """Кнопка 'Встал' — номер начинает отстаивать 25 минут"""
    sub_id = int(callback.data.replace("res_ok_", ""))
    
    # Ставим статус standing и сохраняем время
    data = load_data()
    sub = None
    for s in data["submissions"]:
        if s["id"] == sub_id:
            s["status"] = "standing"
            s["stood_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            s["processed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sub = s
            break
    save_data(data)
    
    if sub:
        # Определяем время отстоя по тарифу
        stand_minutes = TARIFF_STAND_MINUTES.get(sub["tariff"], STAND_TIME_MINUTES)
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_OK} Ваш номер встал!\n\n"
                    f"📱 Номер: {sub['phone_number']}\n"
                    f"💳 Тариф: {sub['tariff']}\n"
                    f"⏱ Ожидайте {stand_minutes} минут для отстоя."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")
        
        # Кнопки после "Встал": SMS и Слет
        standing_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 SMS", callback_data=f"res_sms_{sub_id}"),
                InlineKeyboardButton(text="❌ Слет", callback_data=f"res_slet_{sub_id}"),
            ]
        ])
        
        await callback.message.edit_text(
            f"{E_OK} Номер встал! Отстой {stand_minutes} мин.\n\n"
            f"🔢 Номер: {sub['phone_number']}\n"
            f"💳 Тариф: {sub['tariff']}\n"
            f"🕒 Встал: {sub['stood_at']}\n"
            f"📊 Статус: Отстаивает...",
            reply_markup=standing_keyboard,
            parse_mode="HTML"
        )
        
        # Запускаем таймер по тарифу
        asyncio.create_task(auto_stand_check(sub_id, bot, stand_minutes))
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден")
    await callback.answer()

@router.callback_query(F.data.startswith("res_err_"))
async def result_error_handler(callback: CallbackQuery, bot: Bot):
    """Кнопка 'Ошибка' — ошибка с номером, пользователь должен поставить заново"""
    sub_id = int(callback.data.replace("res_err_", ""))
    sub = update_submission_status(sub_id, "error")
    
    if sub:
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_ERROR} Ваш номер — ошибка!\n\n"
                    f"📱 Номер: {sub['phone_number']}\n"
                    f"Поставьте заново."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")
        
        await callback.message.edit_text(
            f"{E_ERROR} Ошибка с номером!\n\n"
            f"🔢 Номер: {sub['phone_number']}\n"
            f"📊 Статус: Ошибка (пользователь уведомлен)",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден")
    await callback.answer()

@router.callback_query(F.data.startswith("res_retry_"))
async def result_retry_handler(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Кнопка 'Повтор' — ждем новый код, просим админа отправить фото ещё раз"""
    sub_id = int(callback.data.replace("res_retry_", ""))
    
    # Ищем номер в данных
    data = load_data()
    sub = None
    for s in data["submissions"]:
        if s["id"] == sub_id:
            sub = s
            break
    
    if sub:
        # Уведомляем пользователя подождать
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_RETRY} Подождите код в течение 1 минуты.\n\n"
                    f"📱 Номер: {sub['phone_number']}"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя: {e}")
        
        # Просим админа отправить новое фото
        await state.update_data(sub_id=sub_id)
        await state.set_state(AdminStates.waiting_for_image)
        
        await callback.message.edit_text(
            f"{E_RETRY} Повтор! Пользователь уведомлен.\n"
            f"🔢 Номер: {sub['phone_number']}\n\n"
            f"📷 Отправьте новое изображение (скриншот кода)...",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден")
    await callback.answer()

# ----------------------------------------------------------------------------
# Кнопка "Слет" (после "Встал") — слет номера из кнопки
# ----------------------------------------------------------------------------

@router.callback_query(F.data.startswith("res_slet_"))
async def result_slet_handler(callback: CallbackQuery, bot: Bot):
    """Кнопка 'Слет' — пометить номер как слет"""
    sub_id = int(callback.data.replace("res_slet_", ""))
    
    data = load_data()
    sub = None
    for s in data["submissions"]:
        if s["id"] == sub_id and s["status"] == "standing":
            now = datetime.now()
            s["status"] = "slet"
            s["slet_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            sub = s
            write_stand_log(s["phone_number"], s["stood_at"], now, "слет")
            break
    
    if sub:
        save_data(data)
        
        # Уведомляем клиента
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_SLET} Ваш номер — слет!\n\n"
                    f"📱 Номер: {sub['phone_number']}\n"
                    f"Поставьте заново."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления клиента о слете: {e}")
        
        await callback.message.edit_text(
            f"{E_SLET} Номер слетел!\n\n"
            f"🔢 Номер: {sub['phone_number']}\n"
            f"💳 Тариф: {sub['tariff']}\n"
            f"📊 Статус: Слет",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден или уже не отстаивает")
    await callback.answer()

# ----------------------------------------------------------------------------
# Кнопка SMS — отправить сообщение клиенту
# ----------------------------------------------------------------------------

@router.callback_query(F.data.startswith("res_sms_"))
async def result_sms_handler(callback: CallbackQuery, state: FSMContext):
    """Кнопка 'SMS' — запросить текст для отправки клиенту"""
    sub_id = int(callback.data.replace("res_sms_", ""))
    
    data = load_data()
    sub = None
    for s in data["submissions"]:
        if s["id"] == sub_id:
            sub = s
            break
    
    if sub:
        await state.update_data(sms_sub_id=sub_id, sms_user_id=sub["user_id"], sms_phone=sub["phone_number"], sms_status=sub["status"])
        await state.set_state(AdminStates.waiting_for_sms)
        
        await callback.message.answer(
            f"💬 Отправка SMS клиенту\n\n"
            f"📱 Номер: {sub['phone_number']}\n\n"
            f"✍️ Введите текст сообщения:"
        )
    else:
        await callback.message.answer("❌ Ошибка: номер не найден")
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_sms))
async def sms_text_received(message: Message, state: FSMContext, bot: Bot):
    """Получение текста SMS и отправка клиенту"""
    sms_data = await state.get_data()
    user_id = sms_data.get("sms_user_id")
    phone = sms_data.get("sms_phone")
    msg_text = message.text.strip()
    
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"💬 Сообщение от админа:\n\n{msg_text}"
        )
        await message.answer(
            f"✅ Сообщение отправлено!\n\n"
            f"📱 Номер: {phone}\n"
            f"💬 Текст: {msg_text}"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки SMS клиенту: {e}")
        await message.answer("❌ Не удалось отправить сообщение клиенту.")
    
    await state.clear()

# ----------------------------------------------------------------------------
# Команда /slet для группы — слет номера
# ----------------------------------------------------------------------------

@router.message(Command("slet"))
async def slet_command(message: Message, bot: Bot):
    """Обработчик команды /slet <номер> — пометить конкретный номер как слет"""
    
    # Проверяем, что команда вызвана в группе
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("⚠️ Эта команда работает только в группах!")
        return
    
    if not is_allowed_group(message.chat.id):
        return
    
    # Проверяем, что пользователь — админ бота или суперадмин
    user_username = message.from_user.username or ""
    if not is_super_admin(username=user_username) and not is_bot_admin(message.from_user.id):
        await message.answer("⚠️ У вас нет прав! Попросите суперадмина выдать вам доступ через /giveadmin")
        return
    
    # Извлекаем номер из аргумента команды
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "⚠️ Укажите номер!\n\n"
            "Формат: /slet +77001234567"
        )
        return
    
    phone_input = args[1].strip()
    is_valid, phone_number, error_msg = validate_kz_phone(phone_input)
    
    if not is_valid:
        await message.answer(
            "⚠️ Неверный формат номера!\n\n"
            "Формат: /slet +77001234567"
        )
        return
    
    # Ищем этот номер со статусом "standing"
    data = load_data()
    sub = None
    for s in data["submissions"]:
        if s["phone_number"] == phone_number and s["status"] == "standing":
            now = datetime.now()
            s["status"] = "slet"
            s["slet_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            sub = s
            write_stand_log(s["phone_number"], s["stood_at"], now, "слет")
            break
    
    if not sub:
        await message.answer(
            f"❌ Номер {phone_number} не найден среди отстаивающих."
        )
        return
    
    save_data(data)
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            chat_id=sub["user_id"],
            text=(
                f"{E_SLET} Ваш номер — слет!\n\n"
                f"📱 Номер: {sub['phone_number']}\n"
                f"Поставьте заново."
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя о слете: {e}")
    
    await message.answer(
        f"{E_SLET} Номер слетел!\n\n"
        f"🔢 Номер: {sub['phone_number']}\n"
        f"👤 User ID: {sub['user_id']}\n"
        f"📊 Статус: Слет",
        parse_mode="HTML"
    )

# ----------------------------------------------------------------------------
# Команда /msg для группы — отправить сообщение клиенту
# ----------------------------------------------------------------------------

@router.message(Command("msg"))
async def msg_command(message: Message, bot: Bot):
    """Обработчик команды /msg <номер> <текст> — отправить сообщение клиенту"""
    
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("⚠️ Эта команда работает только в группах!")
        return
    
    if not is_allowed_group(message.chat.id):
        return
    
    # Проверяем, что пользователь — админ бота или суперадмин
    user_username = message.from_user.username or ""
    if not is_super_admin(username=user_username) and not is_bot_admin(message.from_user.id):
        await message.answer("⚠️ У вас нет прав! Попросите суперадмина выдать вам доступ через /giveadmin")
        return
    
    # Разбираем аргументы: /msg +77001234567 текст сообщения
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "⚠️ Формат команды:\n\n"
            "/msg +77001234567 Текст сообщения"
        )
        return
    
    phone_input = args[1].strip()
    msg_text = args[2].strip()
    
    is_valid, phone_number, error_msg = validate_kz_phone(phone_input)
    
    if not is_valid:
        await message.answer(
            "⚠️ Неверный формат номера!\n\n"
            "Формат: /msg +77001234567 Текст сообщения"
        )
        return
    
    # Ищем клиента по номеру (последняя заявка с этим номером)
    data = load_data()
    user_id = None
    for s in reversed(data["submissions"]):
        if s["phone_number"] == phone_number:
            user_id = s["user_id"]
            break
    
    if not user_id:
        await message.answer(f"❌ Клиент с номером {phone_number} не найден.")
        return
    
    # Отправляем сообщение клиенту
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"💬 Сообщение от админа:\n\n{msg_text}"
        )
        await message.answer(
            f"✅ Сообщение отправлено!\n\n"
            f"📱 Номер: {phone_number}\n"
            f"💬 Текст: {msg_text}"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения клиенту: {e}")
        await message.answer(f"❌ Не удалось отправить сообщение клиенту.")

@router.callback_query(F.data.startswith("num_skip_"))
async def num_skip_handler(callback: CallbackQuery, bot: Bot):
    """Обработчик кнопки 'Скип'"""
    sub_id = int(callback.data.replace("num_skip_", ""))
    sub = update_submission_status(sub_id, "skipped")
    
    if sub:
        # Уведомляем клиента
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_SKIP} Ваш номер пропущен!\n\n"
                    f"📱 Номер: {sub['phone_number']}\n"
                    f"Поставьте заново."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления клиента о скипе: {e}")
        
        await callback.message.edit_text(
            f"{E_SKIP} Номер пропущен!\n\n"
            f"🔢 Номер: {sub['phone_number']}\n"
            f"💳 Тариф: {sub['tariff']}\n"
            f"📊 Статус: Пропущен",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден")
    await callback.answer()

@router.callback_query(F.data.startswith("num_error_"))
async def num_error_handler(callback: CallbackQuery, bot: Bot):
    """Обработчик кнопки 'Ошибка'"""
    sub_id = int(callback.data.replace("num_error_", ""))
    sub = update_submission_status(sub_id, "error")
    
    if sub:
        # Уведомляем клиента
        try:
            await bot.send_message(
                chat_id=sub["user_id"],
                text=(
                    f"{E_ERROR} Ваш номер — ошибка!\n\n"
                    f"📱 Номер: {sub['phone_number']}\n"
                    f"Поставьте заново."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления клиента об ошибке: {e}")
        
        await callback.message.edit_text(
            f"{E_ERROR} Ошибка с номером!\n\n"
            f"🔢 Номер: {sub['phone_number']}\n"
            f"💳 Тариф: {sub['tariff']}\n"
            f"📊 Статус: Ошибка",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("❌ Ошибка: номер не найден")
    await callback.answer()

# ----------------------------------------------------------------------------
# Узнать ID кастомного эмодзи
# ----------------------------------------------------------------------------

@router.message(F.entities)
async def get_custom_emoji_id(message: Message):
    """Показать ID кастомных эмодзи из сообщения"""
    emoji_ids = []
    for entity in message.entities:
        if entity.type == "custom_emoji":
            emoji_ids.append(entity.custom_emoji_id)
    
    if emoji_ids:
        text = "🆔 ID эмодзи:\n\n"
        for eid in emoji_ids:
            text += f"`{eid}`\n"
        await message.answer(text, parse_mode="Markdown")

# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Главная функция запуска бота"""
    # Инициализация JSON файла
    init_data()
    
    # Создание бота и диспетчера (с прокси если указан)
    proxy_url = os.getenv("PROXY_URL", "")
    if proxy_url:
        connector = ProxyConnector.from_url(proxy_url)
        session = AiohttpSession(connector=connector)
        bot = Bot(token=BOT_TOKEN, session=session)
        logger.info(f"Бот запущен через прокси: {proxy_url}")
    else:
        bot = Bot(token=BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрация роутера
    dp.include_router(router)
    
    logger.info("Бот запущен")
    
    # Запуск polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
