import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
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
            text TEXT NOT NULL,
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

def save_incident(text):
    logger.info(f"Сохранение инцидента: '{text}'")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO incidents (text) VALUES (?)", (text,))
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
    cur.execute("SELECT id, text FROM incidents ORDER BY id DESC LIMIT 1")
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
        "Вы будете получать важную информацию о происшествиях и местах сбора."
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    logger.info(f"/help от user_id={message.from_user.id}")
    await message.answer(
        "/notify <текст> — отправить экстренное уведомление (только для администратора)\n"
        "/report — получить отчет по последнему происшествию (только для администратора)\n"
        "/init_admins — инициализировать список админов из админов группы (выполнять только в группе)"
    )

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

    incident_id = save_incident(command.args)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Пойду", callback_data=f"go_{incident_id}"),
        InlineKeyboardButton(text="Не могу", callback_data=f"no_{incident_id}"),
        InlineKeyboardButton(text="Отправить геолокацию", callback_data=f"geo_{incident_id}")
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

@dp.callback_query(lambda c: c.data and c.data.startswith(("go_", "no_", "geo_")))
async def inline_response(call: types.CallbackQuery):
    action, incident_id = call.data.split("_")
    incident_id = int(incident_id)
    user_id = call.from_user.id
    logger.info(f"inline_response: action={action}, incident_id={incident_id}, user_id={user_id}")

    if action == "go":
        save_response(incident_id, user_id, "Пойду")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отправить геолокацию", callback_data=f"geo_{incident_id}")]
        ])
        await call.message.edit_reply_markup(reply_markup=kb)
        await call.answer("Спасибо, ваш отклик зафиксирован! Можете отправить свою геолокацию.")
    elif action == "no":
        save_response(incident_id, user_id, "Не могу")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Спасибо, ваш отклик зафиксирован.")
    elif action == "geo":
        await call.message.reply(
            "Пожалуйста, отправьте свою геолокацию через вложение (кнопку 📎 — Местоположение).",
            reply_markup=ReplyKeyboardRemove()
        )
        await call.answer()

@dp.message(lambda m: m.location is not None)
async def handle_location(message: types.Message):
    logger.info(f"Получена геолокация от user_id={message.from_user.id}: {message.location}")
    last_incident = get_last_incident()
    if not last_incident:
        await message.answer("Нет активного происшествия, для которого нужна геолокация.")
        return
    incident_id = last_incident[0]
    save_response(incident_id, message.from_user.id, "Пойду", message.location.latitude, message.location.longitude)
    await message.answer("Спасибо, ваша геолокация получена!", reply_markup=ReplyKeyboardRemove())

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

    responses, missed = get_report(incident[0])
    text = f"<b>Отчет по происшествию:</b>\n{incident[1]}\n\n"
    if responses:
        text += "<b>Откликнулись:</b>\n"
        for fname, username, status, lat, lon, _ in responses:
            who = fname or username or "-"
            loc_text = f" [гео]" if lat and lon else ""
            text += f" - {who}: {status}{loc_text}\n"
    if missed:
        text += "\n<b>Не ответили:</b>\n"
        for uid, fname, username in missed:
            who = fname or username or "-"
            text += f" - {who}\n"
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