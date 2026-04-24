import asyncio
import re
import os
import json
import tempfile
import subprocess
import traceback
from aiogram import Router, types, F
from aiogram.filters import Command
from config import config
from typing import Any
from memory import Database
from vector_store import VectorMemory
from prompts import build_system_prompt
from analyzer import analyze_session

# Whisper for offline/local transcription (open-source). Requires `ffmpeg` available in PATH.
# We avoid loading the model at import time to keep memory low on small hosts (Render).
whisper = None
_WHISPER_MODEL = None

router = Router()
db: Database = None
mistral: Any = None
vector_memory: VectorMemory = None

# Инициализация внешних зависимостей (вызывается из bot.py)
def setup_handlers(database: Database, client: Any, vec_mem: VectorMemory):
    global db, mistral, vector_memory
    db = database
    mistral = client
    vector_memory = vec_mem


async def _process_user_text(message: types.Message, text: str):
    """Core text processing: save message, embed, search, call Mistral, reply, update memory."""
    user_id = message.from_user.id

    # 1. Сохраняем сообщение пользователя в сессии
    db.add_session_message(user_id, "user", text)

    # 2. Получаем эмбеддинг текущего сообщения
    try:
        emb_resp = await mistral.embeddings.create_async(
            model=config.mistral_embed_model,
            input=[text]
        )
        query_embedding = emb_resp.data[0].embedding
    except Exception as e:
        await message.answer("Извините, произошла техническая ошибка. Попробуйте позже.")
        print(f"Embedding error: {e}")
        return

    # 3. Поиск похожих фрагментов
    similar = await vector_memory.search_similar(user_id, query_embedding, top_k=3)
    history_str = ""
    for user_msg, bot_resp in similar:
        history_str += f"Пользователь: {user_msg}\nБот: {bot_resp}\n---\n"

    # 4. Формируем системный промпт
    profile_data = await db.get_profile(user_id)
    current_state = profile_data["session_state"]
    profile_json = profile_data["profile"]
    system = build_system_prompt(profile_json, history_str, current_state)

    # 5. Собираем историю последних 10 сообщений из сессии
    recent_msgs = db.get_session_messages(user_id)[-10:]
    mistral_messages = [{"role": "system", "content": system}]
    for m in recent_msgs:
        mistral_messages.append(m)

    # 6. Запрос к Mistral
    try:
        response = await mistral.chat.complete_async(
            model=config.mistral_chat_model,
            messages=mistral_messages,
            temperature=0.7,
            max_tokens=1024
        )
        bot_answer = response.choices[0].message.content.strip()
    except Exception as e:
        await message.answer("Ошибка при генерации ответа. Попробуйте позже.")
        print(f"Mistral chat error: {e}")
        return

    # 7. Извлечение этапа из ответа
    new_state = current_state
    match = re.search(r'\[ЭТАП:\s*(запрос|контекст|чувства|смыслы|ресурс)\]', bot_answer)
    if match:
        new_state = match.group(1).lower()
        bot_answer = re.sub(r'\[ЭТАП:\s*(запрос|контекст|чувства|смыслы|ресурс)\]\s*', '', bot_answer).strip()

    # 8. Отправка ответа пользователю
    await message.answer(bot_answer)

    # 9. Сохраняем ответ бота в сессии
    db.add_session_message(user_id, "assistant", bot_answer)

    # 10. Сохраняем в векторную память (сообщение пользователя + ответ)
    await vector_memory.add_memory(user_id, text, bot_answer, query_embedding)

    # 11. Обновляем этап в базе
    await db.update_session_state(user_id, new_state)

    # 12. Фоновый анализ при достижении N сообщений пользователя
    user_msg_count = db.count_user_messages_in_session(user_id)
    if user_msg_count > 0 and user_msg_count % config.summary_message_count == 0:
        asyncio.create_task(background_analyze(user_id))


def detect_crisis(text: str) -> bool:
    text_lower = text.lower()
    for kw in config.crisis_keywords:
        if re.search(re.escape(kw), text_lower):
            return True
    return False

CRISIS_RESPONSE = (
    "🚨 Я слышу, что Вам сейчас очень тяжело, и это серьёзно. "
    "Пожалуйста, не оставайтесь одни с этими мыслями.\n\n"
    "Вы ценны, и Ваша жизнь имеет значение. Я рядом, чтобы выслушать. Расскажите, что происходит прямо сейчас."
)

CONSENT_KEYBOARD = types.InlineKeyboardMarkup(inline_keyboard=[
    [types.InlineKeyboardButton(text="✅ Согласен", callback_data="consent_yes")],
    [types.InlineKeyboardButton(text="❌ Нет", callback_data="consent_no")]
])


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    # Создать профиль при необходимости
    profile_data = await db.get_profile(user_id)
    if profile_data is None:
        await db.create_user(user_id)
        await message.answer(
            "👋 Здравствуйте! Я — ИИ-психолог, созданный для поддержки и беседы.\n"
            "Я не заменяю настоящего специалиста, но могу выслушать, помочь разобраться в чувствах и найти ресурсы.\n\n"
            "⚠️ Для работы я буду хранить Ваш профиль и историю диалогов. Все данные остаются локально и не передаются третьим лицам, "
            "кроме анонимных запросов к языковой модели Mistral AI.\n\n"
            "Пожалуйста, дайте согласие на хранение и обработку данных:",
            reply_markup=CONSENT_KEYBOARD
        )
    else:
        if profile_data["consent"]:
            await message.answer(
                "С возвращением! Я помню Вас. Мы можем продолжить нашу беседу. Если хотите начать заново, используйте /new_session."
            )
        else:
            await message.answer(
                "Вы ещё не дали согласие на обработку данных. Пожалуйста, подтвердите его, чтобы я мог работать.",
                reply_markup=CONSENT_KEYBOARD
            )


@router.callback_query(F.data == "consent_yes")
async def consent_yes(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await db.update_consent(user_id, True)
    await callback.message.edit_text("✅ Согласие получено! Мы можем начинать. Просто расскажите, что Вас беспокоит или с чем Вы пришли.")
    await callback.answer()


@router.callback_query(F.data == "consent_no")
async def consent_no(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    # Оставляем consent=False, можем удалить данные по желанию
    await callback.message.edit_text(
        "Вы отказались от использования бота. Ваши данные не будут обрабатываться. "
        "Если передумаете, просто нажмите /start и дайте согласие."
    )
    await callback.answer()


@router.message(Command("new_session"))
async def new_session(message: types.Message):
    user_id = message.from_user.id
    profile = await db.get_profile(user_id)
    if profile and profile["consent"]:
        db.clear_session(user_id)
        await db.update_session_state(user_id, "запрос")
        await message.answer("🔄 Начинаем новую сессию. Я готов слушать. Что Вас привело сегодня?")
    else:
        await message.answer("Сначала дайте согласие через /start.")


@router.message(Command("profile"))
async def show_profile(message: types.Message):
    user_id = message.from_user.id
    data = await db.get_profile(user_id)
    if not data or not data["consent"]:
        await message.answer("Профиль недоступен. Дайте согласие через /start.")
        return
    profile = data["profile"]
    state = data["session_state"]
    text = f"📋 **Ваш текущий профиль**\nТекущий этап: {state}\n\n"
    if profile:
        text += json.dumps(profile, ensure_ascii=False, indent=2)
    else:
        text += "Пока пусто."
    await message.answer(text[:4000])  # лимит Telegram


@router.message(Command("delete_data"))
async def delete_data(message: types.Message):
    user_id = message.from_user.id
    await db.delete_user(user_id)
    await vector_memory.delete_user_memories(user_id)
    await message.answer("🗑 Все Ваши данные удалены. Вы можете начать заново с /start.")


@router.message(Command("summarize"))
async def force_summarize(message: types.Message):
    user_id = message.from_user.id
    profile_data = await db.get_profile(user_id)
    if not profile_data or not profile_data["consent"]:
        await message.answer("Нет активного согласия.")
        return
    session_msgs = db.get_session_messages(user_id)
    if not session_msgs:
        await message.answer("Сессия пуста, нечего анализировать.")
        return
    await message.answer("⏳ Анализирую текущую сессию...")
    # Запускаем анализ
    update = await analyze_session(mistral, session_msgs)
    if update:
        await db.update_profile(user_id, update)
        await message.answer("✅ Профиль обновлён.")
    else:
        await message.answer("ℹ️ Новой информации не найдено.")


@router.message(F.text)
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    # Проверка consent
    profile_data = await db.get_profile(user_id)
    if profile_data is None or not profile_data["consent"]:
        await message.answer("Пожалуйста, сначала дайте согласие на обработку данных через /start.",
                             reply_markup=CONSENT_KEYBOARD)
        return

    # Проверка кризисных слов
    if detect_crisis(message.text):
        await message.answer(CRISIS_RESPONSE)
        return

    await _process_user_text(message, message.text)


@router.message(F.voice)
async def handle_voice(message: types.Message):
    """Обработка голосовых сообщений: скачиваем, конвертируем, транскрибируем, обрабатываем как текст."""
    user_id = message.from_user.id
    profile_data = await db.get_profile(user_id)
    if profile_data is None or not profile_data["consent"]:
        await message.answer("Пожалуйста, сначала дайте согласие на обработку данных через /start.",
                             reply_markup=CONSENT_KEYBOARD)
        return

    await message.answer("📥 Получил голосовое сообщение, расшифровываю...")

    ogg_path = None
    wav_path = None
    try:
        # Скачиваем файл во временный ogg
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
            ogg_path = tf.name
        await message.voice.download(destination_file=ogg_path)

        # Конвертируем в wav (whisper ожидает wav/pcm или ffmpeg-совместимый вход)
        wav_path = ogg_path + ".wav"
        subprocess.run(["ffmpeg", "-y", "-i", ogg_path, wav_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Транскрипция (lazy load модели Whisper в отдельном потоке)
        global whisper, _WHISPER_MODEL
        if _WHISPER_MODEL is None:
            try:
                if whisper is None:
                    whisper = __import__("whisper")
                # Загрузка модели в поток, чтобы не блокировать цикл событий
                _WHISPER_MODEL = await asyncio.to_thread(whisper.load_model, "tiny")
            except Exception as _e:
                print("Warning: failed to load Whisper model:", _e)
                await message.answer("Модель для транскрипции недоступна на сервере. Установите `openai-whisper` и убедитесь, что `ffmpeg` доступен.")
                return
        result = await asyncio.to_thread(_WHISPER_MODEL.transcribe, wav_path)
        text = result.get("text", "").strip()
        if not text:
            await message.answer("Не удалось распознать речь. Попробуйте записать ещё раз чуть громче и без фоновых шумов.")
            return

        await message.answer(f"📝 Расшифровка: {text}")
        await _process_user_text(message, text)
    except Exception as e:
        await message.answer("Ошибка при обработке голосового сообщения.")
        print("Voice processing error:", e, traceback.format_exc())
    finally:
        for p in (ogg_path, wav_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def background_analyze(user_id: int):
    """Фоновый анализ сессии и обновление профиля."""
    session_msgs = db.get_session_messages(user_id)
    if not session_msgs:
        return
    update = await analyze_session(mistral, session_msgs)
    if update:
        await db.update_profile(user_id, update)
