import asyncio
import logging
import json
import re
import sys
from datetime import datetime, time as dt_time
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import aiohttp
import os

load_dotenv()

# --- Конфигурация ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
HELPLINE_1 = os.getenv("HELPLINE_1", "8-800-2000-122")
HELPLINE_2 = os.getenv("HELPLINE_2", "8-800-333-44-34")
DB_PATH = Path(__file__).parent / "psy_ai.db"

if not BOT_TOKEN:
    sys.exit("Ошибка: TELEGRAM_BOT_TOKEN не задан в .env")
if not MISTRAL_API_KEY:
    sys.exit("Ошибка: MISTRAL_API_KEY не задан в .env")

# ----- Загрузка системного промта -----
PROMPT_FILE = Path(__file__).parent / "promt.txt"
try:
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        raw_prompt = f.read()
except FileNotFoundError:
    logging.error("Файл promt.txt не найден! Использую резервный промт.")
    raw_prompt = "Ты ИИ-психолог..."

SYSTEM_PROMPT = raw_prompt.replace("{helpline_1}", HELPLINE_1).replace("{helpline_2}", HELPLINE_2)

# ----- Ключевые слова для экстренного прерывания -----
CRISIS_KEYWORDS = re.compile(
    r'(суицид|самоубийств|хочу умереть|убью себя|наложу на себя руки|'
    r'не хочу жить|смерть|покончить с собой|нанести себе вред|'
    r'порезать себя|убить|уничтожить себя)',
    re.IGNORECASE
)

# ----- Инициализация бота и диспетчера с FSM -----
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
logging.basicConfig(level=logging.INFO)

# ----- Состояния для теста GAD-7 (7 вопросов) -----
class GAD7(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()
    q5 = State()
    q6 = State()
    q7 = State()

# ----- Состояния для дневника -----
class Diary(StatesGroup):
    entry = State()

# ----- Клавиатура главного меню -----
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💬 Консультация"), KeyboardButton(text="📊 Тест GAD-7")],
        [KeyboardButton(text="🧘 Дыхание"), KeyboardButton(text="📝 Дневник")],
        [KeyboardButton(text="ℹ️ Профиль"), KeyboardButton(text="🚨 Экстренная помощь")],
    ],
    resize_keyboard=True
)

# ----- Инициализация БД -----
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                profile TEXT DEFAULT '',
                history TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS diary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                entry TEXT,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                user_id INTEGER PRIMARY KEY,
                time TEXT,
                active INTEGER DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        await db.commit()

# ----- Работа с пользовательскими данными -----
async def get_user_data(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT profile, history, message_count FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {
                "profile": row[0],
                "history": json.loads(row[1]),
                "message_count": row[2]
            }
        return {"profile": "", "history": [], "message_count": 0}

async def save_user_data(user_id: int, profile: str, history: list, message_count: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, profile, history, message_count, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, profile, json.dumps(history, ensure_ascii=False), message_count, datetime.now().isoformat())
        )
        await db.commit()

# ----- Запрос к Mistral API -----
async def call_mistral(messages: list, model: str = MISTRAL_MODEL) -> str:
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "top_p": 0.9
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers=headers,
            json=payload
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logging.error(f"Mistral API error {resp.status}: {error_text}")
                raise RuntimeError(f"Mistral API error {resp.status}: {error_text}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

# ----- Агрегация профиля -----
async def update_profile(user_id: int, current_profile: str, last_history_segment: list) -> str:
    if len(last_history_segment) < 4:
        return current_profile
    extract_prompt = (
        "Ты аналитический ассистент. Из диалога психолога с клиентом выдели ключевую информацию "
        "о клиенте: значимые события, триггеры, эмоциональные паттерны, личные цели, предпочитаемый "
        "стиль общения. Сформулируй сжатый профиль на русском языке (до 500 символов), "
        "который можно добавить к существующему профилю. "
        "Не повторяй уже имеющееся, если оно перекрывается. "
        f"Существующий профиль: {current_profile}\n\n"
        "Диалог (последние сообщения):\n" +
        "\n".join(f"{m['role']}: {m['content']}" for m in last_history_segment[-6:])
    )
    messages = [
        {"role": "system", "content": extract_prompt},
        {"role": "user", "content": "Пожалуйста, извлеки профиль из диалога выше."}
    ]
    try:
        new_info = await call_mistral(messages, model=MISTRAL_MODEL)
    except Exception as e:
        logging.error(f"Ошибка при извлечении профиля для user {user_id}: {e}")
        return current_profile

    combined = (current_profile + " " + new_info).strip()
    if len(combined) > 2000:
        combined = combined[:1997] + "..."
    return combined

# ----- Формирование сообщений для API -----
def build_messages(profile: str, history: list, user_text: str) -> list:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    if profile.strip():
        user_message = f"[ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ]\n{profile}\n[КОНЕЦ ПРОФИЛЯ]\n\n{user_text}"
    else:
        user_message = user_text
    messages.append({"role": "user", "content": user_message})
    return messages

# ========== ОБРАБОТЧИКИ МЕНЮ И ФУНКЦИЙ ==========

@dp.message(Command("menu"))
async def show_menu(message: Message):
    if message.from_user is None:
        return
    await message.answer("Выберите действие:", reply_markup=main_kb)

@dp.message(F.text == "💬 Консультация")
async def start_consultation(message: Message):
    if message.from_user is None:
        return
    await message.answer("Я слушаю вас. Расскажите, что вас беспокоит.")

@dp.message(F.text == "🚨 Экстренная помощь")
async def emergency_help(message: Message):
    if message.from_user is None:
        return
    safety_msg = (
        "Если вам нужна немедленная помощь, пожалуйста, позвоните по одному из этих номеров:\n"
        f"• {HELPLINE_1}\n"
        f"• {HELPLINE_2}\n\n"
        "Вы не одни. Обязательно обратитесь к живому специалисту."
    )
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Позвонить {HELPLINE_1}", url=f"tel:{HELPLINE_1.replace('-', '').replace(' ', '')}")],
        [InlineKeyboardButton(text=f"Позвонить {HELPLINE_2}", url=f"tel:{HELPLINE_2.replace('-', '').replace(' ', '')}")]
    ])
    await message.answer(safety_msg, reply_markup=inline_kb)

# --- Тест GAD-7 ---
GAD7_QUESTIONS = [
    "1. Как часто вы чувствовали нервозность, тревогу или напряжение?",
    "2. Как часто вы не могли остановить или контролировать беспокойство?",
    "3. Как часто вы слишком сильно беспокоились о разных вещах?",
    "4. Как часто вам было трудно расслабиться?",
    "5. Как часто вы были настолько непоседливы, что не могли усидеть на месте?",
    "6. Как часто вы легко раздражались или злились?",
    "7. Как часто вы испытывали чувство страха, словно должно произойти что-то ужасное?"
]

GAD7_OPTIONS = [
    ["0 - Никогда", "1 - Несколько дней", "2 - Больше половины дней", "3 - Почти каждый день"]
]

@dp.message(F.text == "📊 Тест GAD-7")
async def start_gad7(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.set_state(GAD7.q1)
    await message.answer(
        GAD7_QUESTIONS[0],
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

@dp.message(GAD7.q1)
async def process_q1(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q1_score=int(message.text[0]))
    await state.set_state(GAD7.q2)
    await message.answer(GAD7_QUESTIONS[1], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q2)
async def process_q2(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q2_score=int(message.text[0]))
    await state.set_state(GAD7.q3)
    await message.answer(GAD7_QUESTIONS[2], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q3)
async def process_q3(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q3_score=int(message.text[0]))
    await state.set_state(GAD7.q4)
    await message.answer(GAD7_QUESTIONS[3], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q4)
async def process_q4(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q4_score=int(message.text[0]))
    await state.set_state(GAD7.q5)
    await message.answer(GAD7_QUESTIONS[4], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q5)
async def process_q5(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q5_score=int(message.text[0]))
    await state.set_state(GAD7.q6)
    await message.answer(GAD7_QUESTIONS[5], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q6)
async def process_q6(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q6_score=int(message.text[0]))
    await state.set_state(GAD7.q7)
    await message.answer(GAD7_QUESTIONS[6], reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=opt)] for opt in GAD7_OPTIONS[0]],
        resize_keyboard=True, one_time_keyboard=True
    ))

@dp.message(GAD7.q7)
async def process_q7(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    await state.update_data(q7_score=int(message.text[0]))
    data = await state.get_data()
    total = sum(data[f"q{i}_score"] for i in range(1, 8))

    if total <= 4:
        conclusion = "Минимальная тревожность. Похоже, сейчас у вас нет выраженных симптомов тревоги."
    elif total <= 9:
        conclusion = "Умеренная тревожность. Возможно, вам стоит обратить внимание на своё состояние и попробовать техники релаксации."
    elif total <= 14:
        conclusion = "Средняя тревожность. Рекомендуется обсудить эти симптомы с психологом или психотерапевтом."
    else:
        conclusion = "Высокая тревожность. Настоятельно рекомендую обратиться к специалисту, потому что тревога может сильно влиять на качество жизни."

    result_text = (
        f"Результат теста GAD-7: {total} баллов.\n\n"
        f"{conclusion}"
    )
    await message.answer(result_text, reply_markup=main_kb)
    user_data = await get_user_data(message.from_user.id)
    new_profile = user_data["profile"] + f" | GAD-7: {total} баллов ({conclusion[:50]}...)"
    await save_user_data(message.from_user.id, new_profile[:2000], user_data["history"], user_data["message_count"])
    await state.clear()

# --- Дыхательное упражнение ---
@dp.message(F.text == "🧘 Дыхание")
async def breathing_exercise(message: Message):
    if message.from_user is None:
        return
    instructions = (
        "🌬️ **Дыхательное упражнение 4-7-8**\n\n"
        "1. Найдите удобное положение.\n"
        "2. Медленно вдохните через нос на **4 счёта**.\n"
        "3. Задержите дыхание на **7 счётов**.\n"
        "4. Медленно выдохните через рот на **8 счётов**.\n\n"
        "Повторите 3-5 раз. Сосредоточьтесь на дыхании и ощущениях в теле."
    )
    await message.answer(instructions)

# --- Дневник ---
@dp.message(F.text == "📝 Дневник")
async def diary_prompt(message: Message, state: FSMContext):
    if message.from_user is None:
        return
    await state.set_state(Diary.entry)
    await message.answer("Отправьте текстом всё, что хотите записать в дневник. Для отмены нажмите /cancel.")

@dp.message(Command("cancel"))
async def cancel_diary(message: Message, state: FSMContext):
    if message.from_user is None:
        return
    await state.clear()
    await message.answer("Запись в дневник отменена.", reply_markup=main_kb)

@dp.message(Diary.entry)
async def save_diary_entry(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    user_id = message.from_user.id
    entry = message.text
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO diary (user_id, entry, created_at) VALUES (?, ?, ?)",
            (user_id, entry, datetime.now().isoformat())
        )
        await db.commit()
    await message.answer("✅ Запись сохранена в вашем дневнике.", reply_markup=main_kb)
    await state.clear()

# --- Профиль ---
@dp.message(F.text == "ℹ️ Профиль")
async def show_profile(message: Message):
    if message.from_user is None:
        return
    user_data = await get_user_data(message.from_user.id)
    profile = user_data["profile"]
    if profile:
        await message.answer(f"Ваш сохранённый профиль:\n\n{profile}")
    else:
        await message.answer("У вас пока нет сохранённого профиля. Он формируется по мере общения.")

# --- Напоминания ---
async def reminder_scheduler():
    while True:
        now = datetime.now().strftime("%H:%M")
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT user_id FROM reminders WHERE time = ? AND active = 1", (now,)
            )
            rows = await cursor.fetchall()
        for row in rows:
            user_id = row[0]
            try:
                await bot.send_message(user_id, "⏰ Напоминание: пора уделить время себе. Как ваше самочувствие?")
            except Exception as e:
                logging.error(f"Не удалось отправить напоминание user {user_id}: {e}")
        await asyncio.sleep(30)

@dp.message(Command("set_reminder"))
async def set_reminder(message: Message):
    if message.from_user is None or message.text is None:
        return
    user_id = message.from_user.id
    args = message.text.split()
    if len(args) != 2:
        await message.answer("Используйте формат: /set_reminder ЧЧ:ММ (например, /set_reminder 14:30)")
        return
    time_str = args[1]
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        await message.answer("Время должно быть в формате ЧЧ:ММ, например, 09:00")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO reminders (user_id, time, active) VALUES (?, ?, 1)",
            (user_id, time_str)
        )
        await db.commit()
    await message.answer(f"Напоминание установлено на {time_str}. Я буду присылать вам уведомление ежедневно в это время.")

# ----- Основной обработчик сообщений -----
@dp.message(Command("start"))
async def start_command(message: Message):
    if message.from_user is None:
        return
    user_id = message.from_user.id
    user_data = await get_user_data(user_id)
    if user_data["message_count"] == 0:
        await message.answer(
            "Здравствуйте! Я ПСИХО ИИ — виртуальный психолог. "
            "Расскажите, как вас зовут и что привело вас сюда?"
        )
    else:
        await message.answer("Здравствуйте! Я слушаю.", reply_markup=main_kb)

@dp.message()
async def handle_message(message: Message, state: FSMContext):
    if message.from_user is None or message.text is None:
        return
    user_id = message.from_user.id
    user_text = message.text

    # Игнорируем кнопки меню, которые уже обработаны отдельными хендлерами
    if user_text in {"💬 Консультация", "📊 Тест GAD-7", "🧘 Дыхание", "📝 Дневник", "🚨 Экстренная помощь", "ℹ️ Профиль"}:
        return

    # Экстренная проверка
    if CRISIS_KEYWORDS.search(user_text):
        safety_msg = (
            "Я слышу, что вам сейчас невыносимо тяжело, и это требует немедленной помощи живого специалиста. "
            "Пожалуйста, позвоните по телефону доверия:\n"
            f"• {HELPLINE_1}\n"
            f"• {HELPLINE_2}\n\n"
            "Вы не одни. Обязательно обратитесь к психологу или психотерапевту очно. Берегите себя."
        )
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Позвонить {HELPLINE_1}", url=f"tel:{HELPLINE_1.replace('-', '').replace(' ', '')}")],
            [InlineKeyboardButton(text=f"Позвонить {HELPLINE_2}", url=f"tel:{HELPLINE_2.replace('-', '').replace(' ', '')}")]
        ])
        await message.answer(safety_msg, reply_markup=inline_kb)
        return

    # Загружаем данные
    user_data = await get_user_data(user_id)
    profile = user_data["profile"]
    history = user_data["history"]
    message_count = user_data["message_count"]

    # Онбординг (первое сообщение и профиль пуст)
    if message_count == 0 and not profile:
        profile = f"Имя: {user_text}. "
        await message.answer(f"Приятно познакомиться, {user_text}! Теперь расскажите, что вас беспокоит.")
    else:
        messages = build_messages(profile, history, user_text)
        try:
            reply = await call_mistral(messages)
        except Exception as e:
            logging.exception("Mistral call failed")
            reply = "Произошла ошибка при обращении к ИИ. Пожалуйста, попробуйте ещё раз."

        await message.answer(reply)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})

    # Ограничиваем историю
    if len(history) > 20:
        history = history[-20:]

    message_count += 1

    # Обновляем профиль каждые 8 сообщений
    if message_count % 8 == 0 and history:
        try:
            profile = await update_profile(user_id, profile, history[-8:])
        except Exception as e:
            logging.error(f"Profile update failed for user {user_id}: {e}")

    # Сохраняем данные
    try:
        await save_user_data(user_id, profile, history, message_count)
    except Exception as e:
        logging.error(f"Failed to save data for user {user_id}: {e}")

    # Показываем меню
    await message.answer("Что хотите сделать?", reply_markup=main_kb)

# ----- Точка входа -----
async def main():
    await init_db()
    asyncio.create_task(reminder_scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())