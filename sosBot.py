import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup, Message
)
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
DB_FILE = os.getenv("DB_FILE", "security_bot.db")

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("security_bot")

def db_connect():
    return sqlite3.connect(DB_FILE)

def db_init():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_member INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            place TEXT,
            photo_id TEXT,
            dt DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            incident_id INTEGER,
            user_id INTEGER,
            status TEXT,
            lat REAL,
            lon REAL,
            dt DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (incident_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована.")

def save_admin(user_id):
    logger.info(f"Сохраняю user_id={user_id} в admins")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    result = cur.fetchone()
    conn.close()
    logger.info(f"Проверка is_admin для user_id={user_id}: {bool(result)}")
    return bool(result)

def get_group_members():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE is_member=1")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    logger.info(f"Получено {len(users)} участников группы из БД.")
    return users

def save_user(user: types.User):
    logger.info(f"Сохраняется пользователь: id={user.id}, username={user.username}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, is_member) VALUES (?, ?, ?, ?, 1)",
        (user.id, user.username, user.first_name, user.last_name)
    )
    conn.commit()
    conn.close()

def save_incident(description, place, photo_id):
    logger.info(f"Сохранение инцидента: '{description}', место: '{place}', фото: '{photo_id}'")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO incidents (description, place, photo_id) VALUES (?, ?, ?)", (description, place, photo_id))
    i_id = cur.lastrowid
    conn.commit()
    conn.close()
    return i_id

def save_response(incident_id, user_id, status, lat=None, lon=None):
    logger.info(f"Сохраняется отклик: incident_id={incident_id}, user_id={user_id}, status={status}, lat={lat}, lon={lon}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO responses (incident_id, user_id, status, lat, lon) VALUES (?, ?, ?, ?, ?)",
        (incident_id, user_id, status, lat, lon)
    )
    conn.commit()
    conn.close()

def get_last_incident():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT id, description, place, photo_id FROM incidents ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    logger.info(f"Получен последний инцидент: {row}")
    return row

def get_report(incident_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.first_name, u.username, r.status, r.lat, r.lon, u.user_id
        FROM responses r
        JOIN users u ON u.user_id = r.user_id
        WHERE r.incident_id=?
    """, (incident_id,))
    responses = cur.fetchall()
    cur.execute("SELECT user_id, first_name, username FROM users WHERE is_member=1")
    all_users = cur.fetchall()
    conn.close()
    resp_user_ids = set([r[5] for r in responses])
    missed = [u for u in all_users if u[0] not in resp_user_ids]
    logger.info(f"Формируется отчет: {len(responses)} ответивших, {len(missed)} не ответивших.")
    return responses, missed

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# --- Новый блок для управления состоянием "ожидания инцидента" ---
# Состояния: None, "description", "place", "photo", "done"
incident_states = {}
incident_data = {}

def incident_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Создать инцидент")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    return kb

@dp.message(Command("init_admins"))
async def cmd_init_admins(message: types.Message):
    logger.info(f"/init_admins вызвана в чате {message.chat.id} тип={message.chat.type}")
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эту команду можно выполнять только в группе.")
        logger.warning("/init_admins вызвана не в группе")
        return
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        logger.info(f"get_chat_administrators вернул {len(admins)} объектов")
    except Exception as e:
        logger.error(f"Ошибка получения админов: {e}")
        await message.answer(f"Ошибка получения админов: {str(e)}")
        return
    count = 0
    added_ids = []
    for admin in admins:
        u = admin.user
        logger.info(f"Обработка admin: user_id={u.id}, username={u.username}, status={admin.status}, is_bot={u.is_bot}")
        if admin.status in ("administrator", "creator") and not u.is_bot:
            save_admin(u.id)
            count += 1
            added_ids.append(f"{u.full_name or ''} (@{u.username})" if u.username else str(u.id))
    if count:
        admins_list = "\n".join(added_ids)
        await message.answer(f"Добавлено {count} администраторов (включая владельца группы):\n{admins_list}")
        logger.info(f"Добавлено {count} админов: {admins_list}")
    else:
        await message.answer("Не найдено администраторов или владельца для добавления.")
        logger.info("Не найдено администраторов для добавления.")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logger.info(f"/start от user_id={message.from_user.id}")
    save_user(message.from_user)
    await message.answer(
        "Вы подписаны на экстренные уведомления группы безопасности. "
        "Чтобы создать инцидент, нажмите кнопку ниже.",
        reply_markup=incident_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    logger.info(f"/help от user_id={message.from_user.id}")
    await message.answer(
        "/notify <текст> — отправить экстренное уведомление (только для администратора)\n"
        "/report — получить отчет по последнему происшествию (только для администратора)\n"
        "/init_admins — инициализировать список админов из админов группы (выполнять только в группе)\n"
        "В личке используйте кнопку 'Создать инцидент'.",
        reply_markup=incident_keyboard()
    )

# --- Новый обработчик: кнопка "Создать инцидент" ---
@dp.message(lambda m: m.chat.type == "private" and m.text == "Создать инцидент")
async def ask_incident_description(message: types.Message):
    incident_states[message.from_user.id] = "description"
    incident_data[message.from_user.id] = {}
    await message.answer(
        "Пожалуйста, опишите ситуацию (текст инцидента):",
        reply_markup=ReplyKeyboardRemove()
    )

# --- Пошаговый ввод: описание -> место -> фото (опционально) ---
@dp.message(lambda m: m.chat.type == "private" and incident_states.get(m.from_user.id) == "description")
async def receive_incident_description(message: types.Message):
    desc = message.text.strip()
    if not desc:
        await message.answer("Описание не может быть пустым. Пожалуйста, опишите ситуацию.")
        return
    incident_data[message.from_user.id]["description"] = desc
    incident_states[message.from_user.id] = "place"
    await message.answer("Теперь укажите место сбора (например: 'вход №2', 'главная площадь', адрес и т.д.):")

@dp.message(lambda m: m.chat.type == "private" and incident_states.get(m.from_user.id) == "place")
async def receive_incident_place(message: types.Message):
    place = message.text.strip()
    if not place:
        await message.answer("Место не может быть пустым. Пожалуйста, укажите место сбора.")
        return
    incident_data[message.from_user.id]["place"] = place
    incident_states[message.from_user.id] = "photo"
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Пропустить добавление фото")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "Если хотите, прикрепите фото (например, ориентир/место/ситуацию) или нажмите 'Пропустить добавление фото'.",
        reply_markup=kb
    )

@dp.message(lambda m: m.chat.type == "private" and incident_states.get(m.from_user.id) == "photo" and m.text == "Пропустить добавление фото")
async def skip_incident_photo(message: types.Message):
    incident_data[message.from_user.id]["photo_id"] = None
    await finalize_incident_creation(message)

@dp.message(lambda m: m.chat.type == "private" and incident_states.get(m.from_user.id) == "photo" and m.photo)
async def receive_incident_photo(message: types.Message):
    # Берём file_id самой большой версии фото
    largest_photo = max(message.photo, key=lambda p: p.width * p.height)
    photo_id = largest_photo.file_id
    incident_data[message.from_user.id]["photo_id"] = photo_id
    await finalize_incident_creation(message)

@dp.message(lambda m: m.chat.type == "private" and incident_states.get(m.from_user.id) == "photo")
async def not_photo_warning(message: types.Message):
    await message.answer(
        "Пожалуйста, отправьте фото или нажмите 'Пропустить добавление фото'."
    )

async def finalize_incident_creation(message: types.Message):
    user_id = message.from_user.id
    data = incident_data.get(user_id, {})
    desc = data.get("description")
    place = data.get("place")
    photo_id = data.get("photo_id")

    incident_id = save_incident(desc, place, photo_id)

    # Рассылка всем участникам, как в /notify
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Пойду", callback_data=f"go_{incident_id}"),
        InlineKeyboardButton(text="Не могу", callback_data=f"no_{incident_id}")
    )
    count = 0
    text = f"<b>Экстренное сообщение:</b>\n{desc}\n<b>Место сбора:</b> {place}"
    for uid in get_group_members():
        try:
            if photo_id:
                await bot.send_photo(
                    uid,
                    photo=photo_id,
                    caption=text,
                    reply_markup=builder.as_markup(),
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_message(
                    uid,
                    text,
                    reply_markup=builder.as_markup()
                )
            logger.info(f"Уведомление отправлено user_id={uid}")
            count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления user_id={uid}: {e}")

    await message.answer(
        f"Инцидент создан и уведомление отправлено {count} участникам.",
        reply_markup=incident_keyboard()
    )
    incident_states.pop(user_id, None)
    incident_data.pop(user_id, None)

@dp.message(Command("notify"))
async def cmd_notify(message: types.Message, command: CommandObject):
    logger.info(f"/notify от user_id={message.from_user.id} args={command.args}")
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может отправлять уведомления.")
        logger.warning(f"user_id={message.from_user.id} попытался вызвать /notify без прав")
        return

    if not command.args:
        await message.answer("Использование: /notify <текст происшествия>")
        return

    incident_id = save_incident(command.args, "", None)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Пойду", callback_data=f"go_{incident_id}"),
        InlineKeyboardButton(text="Не могу", callback_data=f"no_{incident_id}")
    )
    count = 0
    for user_id in get_group_members():
        try:
            await bot.send_message(
                user_id,
                f"<b>Экстренное сообщение:</b>\n{command.args}",
                reply_markup=builder.as_markup()
            )
            logger.info(f"Уведомление отправлено user_id={user_id}")
            count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления user_id={user_id}: {e}")
    await message.answer(f"Уведомление отправлено {count} участникам.")

@dp.callback_query(lambda c: c.data and c.data.startswith(("go_", "no_")))
async def inline_response(call: types.CallbackQuery):
    action, incident_id = call.data.split("_")
    incident_id = int(incident_id)
    user_id = call.from_user.id
    logger.info(f"inline_response: action={action}, incident_id={incident_id}, user_id={user_id}")

    if action == "go":
        save_response(incident_id, user_id, "Пойду")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Спасибо, ваш отклик зафиксирован!")
    elif action == "no":
        save_response(incident_id, user_id, "Не могу")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Спасибо, ваш отклик зафиксирован.")

@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    logger.info(f"/report от user_id={message.from_user.id}")
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может получать отчет.")
        logger.warning(f"user_id={message.from_user.id} попытался вызвать /report без прав")
        return

    incident = get_last_incident()
    if not incident:
        await message.answer("Нет происшествий.")
        return

    incident_id, desc, place, photo_id = incident
    responses, missed = get_report(incident_id)
    text = f"<b>Отчет по происшествию:</b>\n{desc}\n<b>Место сбора:</b> {place if place else '-'}\n\n"
    if responses:
        text += "<b>Откликнулись:</b>\n"
        for fname, username, status, lat, lon, _ in responses:
            who = fname or username or "-"
            text += f" - {who}: {status}\n"
    if missed:
        text += "\n<b>Не ответили:</b>\n"
        for uid, fname, username in missed:
            who = fname or username or "-"
            text += f" - {who}\n"
    if photo_id:
        await message.answer_photo(photo_id, caption=text, parse_mode=ParseMode.HTML)
    else:
        await message.answer(text)

@dp.message(lambda m: m.chat.type in ("group", "supergroup"))
async def handle_group_message(message: types.Message):
    if message.new_chat_members:
        for user in message.new_chat_members:
            logger.info(f"Добавлен новый участник user_id={user.id}")
            save_user(user)
    if message.left_chat_member:
        logger.info(f"Пользователь покинул группу user_id={message.left_chat_member.id}")
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_member=0 WHERE user_id=?", (message.left_chat_member.id,))
        conn.commit()
        conn.close()

async def main():
    db_init()
    logger.info("Бот запускается...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())