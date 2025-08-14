import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup
)
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

# === НАСТРОЙКИ ===
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
DB_FILE = os.getenv("DB_FILE", "security_bot.db")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-1000000000000"))  # Ваш chat_id группы

# === ВРЕМЯ МОСКВЫ ===
MOSCOW_TZ = timezone(timedelta(hours=3))

def utc_to_msk(dt_str):
    """Преобразует строку UTC-времени из SQLite в строку московского времени."""
    try:
        dt_utc = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_msk = dt_utc.astimezone(MOSCOW_TZ)
        return dt_msk.strftime("%d.%m.%Y %H:%M") + " МСК"
    except Exception:
        return dt_str

# === ЛОГИРОВАНИЕ ===
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
            place TEXT,
            photo_id TEXT,
            dt DATETIME DEFAULT CURRENT_TIMESTAMP,
            stats_msg_id INTEGER
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

def delete_admin(user_id):
    logger.info(f"Удаляю user_id={user_id} из admins")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
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

def get_admins():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]

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

def subscribe_user(user: types.User):
    logger.info(f"Подписка пользователя: id={user.id}, username={user.username}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, is_member) VALUES (?, ?, ?, ?, 1)",
        (user.id, user.username, user.first_name, user.last_name)
    )
    conn.commit()
    conn.close()

def unsubscribe_user(user_id):
    logger.info(f"Отписка пользователя user_id={user_id}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_member=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def save_incident(text, place=None, photo_id=None, stats_msg_id=None):
    logger.info(f"Сохранение инцидента: '{text}', место: '{place}', фото: '{photo_id}', stats_msg_id: {stats_msg_id}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO incidents (text, place, photo_id, stats_msg_id) VALUES (?, ?, ?, ?)",
        (text, place, photo_id, stats_msg_id)
    )
    i_id = cur.lastrowid
    conn.commit()
    conn.close()
    return i_id

def set_incident_stats_msg(incident_id, stats_msg_id):
    logger.info(f"Связываю инцидент {incident_id} с stats_msg_id={stats_msg_id}")
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE incidents SET stats_msg_id=? WHERE id=?", (stats_msg_id, incident_id))
    conn.commit()
    conn.close()

def get_incident_stats_msg_id(incident_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT stats_msg_id FROM incidents WHERE id=?", (incident_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else None

def get_incident_info(incident_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT text, place, photo_id, dt FROM incidents WHERE id=?", (incident_id,))
    row = cur.fetchone()
    conn.close()
    return row

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
    cur.execute("SELECT id, text, place, photo_id, dt FROM incidents ORDER BY id DESC LIMIT 1")
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

def get_recent_incidents(limit=5):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT id, text, dt FROM incidents ORDER BY dt DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    incidents = []
    for row in rows:
        incident_id, text, dt = row
        dt_str = utc_to_msk(dt)
        short_text = text if len(text) < 32 else text[:29] + "..."
        incidents.append({
            "id": incident_id,
            "text": short_text,
            "dt": dt_str
        })
    return incidents

def get_go_members(incident_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.first_name, u.username
        FROM responses r
        JOIN users u ON u.user_id = r.user_id
        WHERE r.incident_id=? AND r.status='Пойду'
    """, (incident_id,))
    rows = cur.fetchall()
    conn.close()
    names = []
    for fname, username in rows:
        if fname:
            names.append(fname)
        elif username:
            names.append(f"@{username}")
    return names

def get_incident_stats_text(incident_id):
    info = get_incident_info(incident_id)
    if not info:
        return "Инцидент не найден."
    description, place, photo_id, dt = info
    dt_str = utc_to_msk(dt)
    text = f"<b>Инцидент:</b> {description}"
    if place:
        text += f"\n<b>Место сбора:</b> {place}"
    text += f"\n<b>Время:</b> {dt_str}\n"
    go_members = get_go_members(incident_id)
    if go_members:
        text += "\n<b>Пойдут:</b>\n"
        for name in go_members:
            text += f" - {name}\n"
    else:
        text += "\n<b>Пойдут:</b> пока никто не откликнулся"
    return text

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

def incident_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Создать инцидент")],
            [KeyboardButton(text="Отписаться от рассылки")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    return kb

def subscribe_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Подписаться на рассылку")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    return kb

incident_creation_state = {}  # user_id: {'step': ..., 'data': {...}}

@dp.message(Command("init_admins"))
async def cmd_init_admins(message: types.Message):
    logger.info(
        f"/init_admins вызвана в чате {message.chat.id} тип={message.chat.type} "
        f"(GROUP_CHAT_ID={GROUP_CHAT_ID}) message_thread_id={getattr(message, 'message_thread_id', None)}"
    )
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
        logger.info(
            f"Обработка admin: user_id={u.id}, username={u.username}, status={admin.status}, "
            f"is_bot={u.is_bot}, from_chat_id={message.chat.id}, "
            f"message_thread_id={getattr(message, 'message_thread_id', None)}"
        )
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

# === НОВЫЕ КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ АДМИНАМИ ===

@dp.message(Command("add_admin"))
async def cmd_add_admin(message: types.Message, command: CommandObject):
    # Только в личке и только админ может добавить другого админа
    if message.chat.type != "private":
        await message.answer("Добавлять админов можно только в личных сообщениях с ботом.")
        return
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может добавлять новых администраторов.")
        logger.warning(f"user_id={message.from_user.id} попытался добавить админа без прав")
        return
    if not command.args:
        await message.answer("Использование: /add_admin <user_id или @username>")
        return

    arg = command.args.strip()
    user_id = None

    # Попробуем распарсить как user_id или username
    if arg.startswith("@"):
        username = arg[1:]
        # Поиск по username в таблице users
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        conn.close()
        if row:
            user_id = row[0]
        else:
            await message.answer(f"Пользователь с username @{username} не найден в базе. Сначала он должен написать боту.")
            return
    else:
        try:
            user_id = int(arg)
        except Exception:
            await message.answer("Некорректный user_id. Используйте /add_admin <user_id или @username>")
            return

    save_admin(user_id)
    await message.answer(f"Пользователь с user_id={user_id} теперь администратор.")
    logger.info(f"user_id={message.from_user.id} добавил админа user_id={user_id}")

    # Уведомление новому админу
    try:
        await bot.send_message(
            user_id,
            "Вам выданы права администратора в системе экстренных уведомлений. "
            "Теперь вы можете создавать инциденты и управлять другими администраторами через команды в личке с этим ботом."
        )
        logger.info(f"Новому админу user_id={user_id} отправлено уведомление в личку.")
    except Exception as e:
        logger.warning(f"Не удалось отправить личное сообщение новому админу user_id={user_id}: {e}")

@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: types.Message, command: CommandObject):
    # Только в личке и только админ может удалять админа
    if message.chat.type != "private":
        await message.answer("Удалять админов можно только в личных сообщениях с ботом.")
        return
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может удалять других администраторов.")
        logger.warning(f"user_id={message.from_user.id} попытался удалить админа без прав")
        return
    if not command.args:
        await message.answer("Использование: /remove_admin <user_id или @username>")
        return

    arg = command.args.strip()
    user_id = None

    if arg.startswith("@"):
        username = arg[1:]
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        conn.close()
        if row:
            user_id = row[0]
        else:
            await message.answer(f"Пользователь с username @{username} не найден в базе.")
            return
    else:
        try:
            user_id = int(arg)
        except Exception:
            await message.answer("Некорректный user_id. Используйте /remove_admin <user_id или @username>")
            return

    if user_id == message.from_user.id:
        await message.answer("Вы не можете удалить сами себя из администраторов.")
        return

    if not is_admin(user_id):
        await message.answer(f"Пользователь с user_id={user_id} не является администратором.")
        return

    delete_admin(user_id)
    await message.answer(f"Пользователь с user_id={user_id} больше не администратор.")
    logger.info(f"user_id={message.from_user.id} удалил админа user_id={user_id}")

@dp.message(Command("list_admins"))
async def cmd_list_admins(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может просматривать список администраторов.")
        return
    admins = get_admins()
    if not admins:
        await message.answer("Список администраторов пуст.")
        return
    text = "<b>Список администраторов:</b>\n"
    conn = db_connect()
    cur = conn.cursor()
    for uid in admins:
        cur.execute("SELECT first_name, username FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        user_desc = f"{uid}"
        if row:
            fname, username = row
            if fname:
                user_desc = fname
            if username:
                user_desc += f" (@{username})"
        text += f"- {user_desc}\n"
    conn.close()
    await message.answer(text)

# === ОСНОВНОЙ ФУНКЦИОНАЛ (оставлен без изменений, кроме help) ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logger.info(f"/start от user_id={message.from_user.id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    save_user(message.from_user)
    await message.answer(
        "Вы подписаны на экстренные уведомления группы безопасности. "
        "Чтобы создать инцидент, нажмите кнопку ниже.\n"
        "Чтобы отписаться — используйте кнопку 'Отписаться от рассылки' или /stop.",
        reply_markup=incident_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    logger.info(f"/help от user_id={message.from_user.id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await message.answer(
        "/notify &lt;текст&gt; — отправить экстренное уведомление (только для администратора)\n"
        "/report — получить отчет по происшествиям (только для администратора)\n"
        "/init_admins — инициализировать список админов из админов группы (выполнять только в группе)\n"
        "/add_admin &lt;user_id или @username&gt; — добавить администратора (только для администратора, в личке)\n"
        "/remove_admin &lt;user_id или @username&gt; — удалить администратора (только для администратора, в личке)\n"
        "/list_admins — показать список админов (только для администратора)\n"
        "/stop — отписаться от экстренной рассылки\n"
        "В личке используйте кнопку 'Создать инцидент'.",
        reply_markup=incident_keyboard()
    )

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    unsubscribe_user(message.from_user.id)
    logger.info(f"user_id={message.from_user.id} отписался от рассылки (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await message.answer(
        "Вы отписались от экстренных уведомлений. Если захотите снова получать рассылку, нажмите кнопку ниже.",
        reply_markup=subscribe_keyboard()
    )

@dp.message(lambda m: m.chat.type == "private" and m.text == "Отписаться от рассылки")
async def handle_unsubscribe(message: types.Message):
    unsubscribe_user(message.from_user.id)
    logger.info(f"user_id={message.from_user.id} отписался от рассылки через кнопку (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await message.answer(
        "Вы отписались от экстренных уведомлений. Если захотите снова получать рассылку, нажмите кнопку ниже.",
        reply_markup=subscribe_keyboard()
    )

@dp.message(lambda m: m.chat.type == "private" and m.text == "Подписаться на рассылку")
async def handle_subscribe(message: types.Message):
    subscribe_user(message.from_user)
    logger.info(f"user_id={message.from_user.id} подписался на рассылку через кнопку (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await message.answer(
        "Вы снова подписаны на экстренные уведомления.",
        reply_markup=incident_keyboard()
    )

@dp.message(lambda m: m.chat.type == "private" and m.text == "Создать инцидент")
async def start_incident_creation(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может создавать инциденты.")
        logger.warning(f"user_id={message.from_user.id} попытался создать инцидент без прав")
        return
    incident_creation_state[message.from_user.id] = {'step': 'description', 'data': {}}
    logger.info(f"user_id={message.from_user.id} начал создание инцидента (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await message.answer("Пожалуйста, опишите ситуацию (текст инцидента):", reply_markup=ReplyKeyboardRemove())

@dp.message(lambda m: m.chat.type == "private" and incident_creation_state.get(m.from_user.id, {}).get('step') == 'description')
async def incident_description(message: types.Message):
    incident_creation_state[message.from_user.id]['data']['description'] = message.text.strip()
    incident_creation_state[message.from_user.id]['step'] = 'place'
    await message.answer("Укажите место сбора (можно текстом или геолокацией):")

@dp.message(lambda m: m.chat.type == "private" and incident_creation_state.get(m.from_user.id, {}).get('step') == 'place' and m.location is not None)
async def incident_place_location(message: types.Message):
    data = incident_creation_state[message.from_user.id]['data']
    data['place'] = f"Геолокация: {message.location.latitude}, {message.location.longitude}"
    incident_creation_state[message.from_user.id]['step'] = 'photo'
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Прикрепите фото (опционально) или нажмите 'Пропустить':", reply_markup=markup)

@dp.message(lambda m: m.chat.type == "private" and incident_creation_state.get(m.from_user.id, {}).get('step') == 'place')
async def incident_place_text(message: types.Message):
    data = incident_creation_state[message.from_user.id]['data']
    data['place'] = message.text.strip()
    incident_creation_state[message.from_user.id]['step'] = 'photo'
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Прикрепите фото (опционально) или нажмите 'Пропустить':", reply_markup=markup)

@dp.message(lambda m: m.chat.type == "private" and incident_creation_state.get(m.from_user.id, {}).get('step') == 'photo' and m.photo is not None)
async def incident_photo(message: types.Message):
    photo = message.photo[-1].file_id
    incident_creation_state[message.from_user.id]['data']['photo'] = photo
    await finish_incident_creation(message)

@dp.message(lambda m: m.chat.type == "private" and incident_creation_state.get(m.from_user.id, {}).get('step') == 'photo' and m.text == "Пропустить")
async def skip_photo(message: types.Message):
    await finish_incident_creation(message)

async def finish_incident_creation(message: types.Message):
    data = incident_creation_state.pop(message.from_user.id)['data']
    description = data.get('description', '')
    place = data.get('place', '')
    photo = data.get('photo', None)
    incident_id = save_incident(description, place, photo, None)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Пойду", callback_data=f"go_{incident_id}"),
        InlineKeyboardButton(text="Не могу", callback_data=f"no_{incident_id}")
    )
    notify_text = f"<b>Экстренное сообщение:</b>\n{description}\n\n<b>Место сбора:</b> {place}"
    count = 0
    for uid in get_group_members():
        try:
            if photo:
                await bot.send_photo(
                    uid,
                    photo=photo,
                    caption=notify_text,
                    reply_markup=builder.as_markup()
                )
            else:
                await bot.send_message(
                    uid,
                    notify_text,
                    reply_markup=builder.as_markup()
                )
            logger.info(f"Уведомление отправлено user_id={uid} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
            count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления user_id={uid} (GROUP_CHAT_ID={GROUP_CHAT_ID}): {e}")

    stats_text = get_incident_stats_text(incident_id)
    try:
        logger.info(f"Пробую отправить статистику в дефолтную тему group_id={GROUP_CHAT_ID}")
        if photo:
            stats_msg = await bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=photo,
                caption=stats_text
                # message_thread_id не указываем!
            )
            stats_msg_id = stats_msg.message_id
        else:
            stats_msg = await bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=stats_text
                # message_thread_id не указываем!
            )
            stats_msg_id = stats_msg.message_id
        logger.info(f"Статистика по инциденту {incident_id} отправлена в дефолтную тему group_id={GROUP_CHAT_ID} (msg_id={stats_msg_id})")
        set_incident_stats_msg(incident_id, stats_msg_id)
    except Exception as e:
        logger.error(f"Ошибка отправки статистики в дефолтную тему group_id={GROUP_CHAT_ID}: {e}")

    await message.answer(f"Инцидент создан и уведомление отправлено {count} участникам.", reply_markup=incident_keyboard())

@dp.callback_query(lambda c: c.data and c.data.startswith(("go_", "no_")))
async def inline_response(call: types.CallbackQuery):
    action, incident_id = call.data.split("_")
    incident_id = int(incident_id)
    user_id = call.from_user.id
    logger.info(f"inline_response: action={action}, incident_id={incident_id}, user_id={user_id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")

    if action == "go":
        save_response(incident_id, user_id, "Пойду")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Спасибо, ваш отклик зафиксирован!")
    elif action == "no":
        save_response(incident_id, user_id, "Не могу")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer("Спасибо, ваш отклик зафиксирован.")

    stats_msg_id = get_incident_stats_msg_id(incident_id)
    if stats_msg_id:
        stats_text = get_incident_stats_text(incident_id)
        info = get_incident_info(incident_id)
        photo_id = info[2] if info else None
        try:
            logger.info(f"Обновляю статистику по инциденту {incident_id} в group_id={GROUP_CHAT_ID}, msg_id={stats_msg_id}")
            if photo_id:
                await bot.edit_message_caption(
                    chat_id=GROUP_CHAT_ID,
                    message_id=stats_msg_id,
                    caption=stats_text,
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=GROUP_CHAT_ID,
                    message_id=stats_msg_id,
                    text=stats_text,
                    parse_mode=ParseMode.HTML
                )
            logger.info(f"Статистика инцидента {incident_id} обновлена в дефолтной теме group_id={GROUP_CHAT_ID}.")
        except Exception as e:
            logger.error(f"Ошибка обновления статистики по инциденту {incident_id} group_id={GROUP_CHAT_ID}: {e}")

@dp.message(Command("notify"))
async def cmd_notify(message: types.Message, command: CommandObject):
    logger.info(f"/notify от user_id={message.from_user.id} (GROUP_CHAT_ID={GROUP_CHAT_ID}) args={command.args}")
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может отправлять уведомления.")
        logger.warning(f"user_id={message.from_user.id} попытался вызвать /notify без прав")
        return

    if not command.args:
        await message.answer("Использование: /notify <текст происшествия>")
        return

    incident_id = save_incident(command.args, None, None, None)
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
            logger.info(f"Уведомление отправлено user_id={user_id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
            count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления user_id={user_id} (GROUP_CHAT_ID={GROUP_CHAT_ID}): {e}")

    stats_text = get_incident_stats_text(incident_id)
    try:
        logger.info(f"Пробую отправить статистику в дефолтную тему group_id={GROUP_CHAT_ID}")
        stats_msg = await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=stats_text
            # message_thread_id не указываем!
        )
        set_incident_stats_msg(incident_id, stats_msg.message_id)
        logger.info(f"Статистика по инциденту {incident_id} отправлена в дефолтную тему group_id={GROUP_CHAT_ID} (msg_id={stats_msg.message_id})")
    except Exception as e:
        logger.error(f"Ошибка отправки статистики в дефолтную тему group_id={GROUP_CHAT_ID}: {e}")

    await message.answer(f"Уведомление отправлено {count} участникам.")

@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    logger.info(f"/report от user_id={message.from_user.id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    if not is_admin(message.from_user.id):
        await message.answer("Только администратор может получать отчет.")
        logger.warning(f"user_id={message.from_user.id} попытался вызвать /report без прав")
        return

    incidents = get_recent_incidents(limit=5)
    if not incidents:
        await message.answer("Нет происшествий.")
        return
    builder = InlineKeyboardBuilder()
    for inc in incidents:
        btn_text = f"{inc['dt']} | {inc['text']}"
        builder.row(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"report_{inc['id']}"
            )
        )
    await message.answer(
        "Выберите происшествие для отчёта:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(lambda c: c.data and c.data.startswith("report_"))
async def report_incident_callback(call: types.CallbackQuery):
    incident_id = int(call.data.split("_")[1])
    logger.info(f"Отправка отчета по инциденту {incident_id} по callback (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    info = get_incident_info(incident_id)
    if not info:
        await call.answer("Инцидент не найден.", show_alert=True)
        return
    description, place, photo_id, dt = info
    dt_str = utc_to_msk(dt)
    responses, missed = get_report(incident_id)
    text = f"<b>Отчет по происшествию:</b>\n{description}"
    if place:
        text += f"\n<b>Место сбора:</b> {place}"
    text += f"\n<b>Время:</b> {dt_str}\n\n"
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
        await call.message.answer_photo(photo=photo_id, caption=text)
    else:
        await call.message.answer(text)
    await call.answer()

@dp.message(lambda m: m.chat.type in ("group", "supergroup"))
async def handle_group_message(message: types.Message):
    logger.info(f"Новое сообщение в группе {message.chat.id} (GROUP_CHAT_ID={GROUP_CHAT_ID}) message_thread_id={getattr(message, 'message_thread_id', None)} text={message.text}")
    if message.new_chat_members:
        for user in message.new_chat_members:
            logger.info(f"Добавлен новый участник user_id={user.id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
            save_user(user)
    if message.left_chat_member:
        logger.info(f"Пользователь покинул группу user_id={message.left_chat_member.id} (GROUP_CHAT_ID={GROUP_CHAT_ID})")
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_member=0 WHERE user_id=?", (message.left_chat_member.id,))
        conn.commit()
        conn.close()

async def main():
    db_init()
    logger.info(f"Бот запускается... (GROUP_CHAT_ID={GROUP_CHAT_ID})")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())