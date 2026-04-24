import logging
import asyncio
from typing import Any, cast
from aiogram import Router, types
from aiogram.filters import Command
from memory import get_user_profile, save_user_profile, delete_user_data
from vector_store import add_message_to_vector, search_similar_messages
from prompts import build_system_prompt
from analyzer import update_profile_from_dialog
from config import ANALYZE_EVERY_N, MAX_HISTORY_MESSAGES, MISTRAL_API_KEY, MISTRAL_MODEL
import mistralai

router = Router()
logger = logging.getLogger(__name__)

# Кризисные стоп-слова
CRISIS_KEYWORDS = ["суицид", "самоубийство", "селфхарм", "самоповрежд", "насилие", "хочу умереть", "убей", "покончить с собой"]
CRISIS_RESPONSE = (
    "⚠️ Я слышу, что вам невыносимо тяжело. Я — всего лишь ИИ-помощник и не могу заменить профессиональную помощь. "
    "Пожалуйста, прямо сейчас обратитесь к живым специалистам:\n"
    "📞 Телефон доверия (Россия): 8-800-2000-122\n"
    "📞 Кризисная линия: +7 (495) 989-50-50\n"
    "Если вы находитесь в другой стране, поищите местные службы поддержки. Вы не один."
)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я — ИИ-психолог (экспериментальный). Я не настоящий специалист, не ставлю диагнозы и не лечу. "
        "Всё, что вы расскажете, останется между нами (в рамках системы). "
        "Вы согласны, что ваши данные будут обрабатываться для работы бота?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree")],
            [types.InlineKeyboardButton(text="❌ Нет", callback_data="disagree")]
        ])
    )

@router.callback_query(lambda c: c.data == "agree")
async def agree_handler(callback: types.CallbackQuery):
    # Сохраняем согласие
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    user_id = callback.from_user.id
    profile = await get_user_profile(user_id)
    profile["agreed"] = True
    await save_user_profile(user_id, profile)
    await callback.message.answer("Спасибо! Теперь вы можете начать беседу. Расскажите, что вас беспокоит.")
    await callback.answer()

@router.callback_query(lambda c: c.data == "disagree")
async def disagree_handler(callback: types.CallbackQuery):
    if callback.message:
        await callback.message.answer("Без согласия на обработку данных я не могу продолжить. Если передумаете — напишите /start.")
    await callback.answer()

@router.message(Command("new_session"))
async def cmd_new_session(message: types.Message):
    if not message.from_user:
        return
    profile = await get_user_profile(message.from_user.id)
    profile["session_state"] = "gathering_request"
    profile["session_messages"] = []  # сброс текущей сессии
    await save_user_profile(message.from_user.id, profile)
    await message.answer("Сессия сброшена. Можете начать новый разговор.")

@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    if not message.from_user:
        return
    profile = await get_user_profile(message.from_user.id)
    summary = f"**Ваш профиль:**\n"
    summary += f"Текущий запрос: {profile.get('current_request', 'не указан')}\n"
    summary += f"Триггеры: {', '.join(profile.get('emotional_triggers', [])) or 'нет'}\n"
    summary += f"Ресурсы: {', '.join(profile.get('resources', [])) or 'нет'}\n"
    await message.answer(summary)

@router.message(Command("delete_data"))
async def cmd_delete_data(message: types.Message):
    if not message.from_user:
        return
    await delete_user_data(message.from_user.id)
    await message.answer("Все ваши данные удалены. Чтобы начать заново, нажмите /start.")

@router.message()
async def handle_message(message: types.Message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    text = (message.text or "").strip()

    # Проверка на кризисные слова (примитивный, но работает)
    if any(word in text.lower() for word in CRISIS_KEYWORDS):
        await message.answer(CRISIS_RESPONSE)
        return

    # Загружаем профиль
    profile = await get_user_profile(user_id)
    if not profile.get("agreed"):
        await message.answer("Пожалуйста, сначала дайте согласие через /start.")
        return

    # Векторный поиск похожих прошлых сообщений
    similar_docs = await search_similar_messages(user_id, text, top_k=3)

    # Формируем системный промпт
    system_prompt = build_system_prompt(profile, similar_docs)

    # История текущей сессии (последние MAX_HISTORY_MESSAGES)
    history = profile.get("session_messages", [])[-MAX_HISTORY_MESSAGES:]

    # Вызов Mistral
    client = mistralai.Mistral(api_key=MISTRAL_API_KEY)
    messages = [{"role": "system", "content": system_prompt}]
    for h_msg in history:
        messages.append(h_msg)
    messages.append({"role": "user", "content": text})

    try:
        # suppress strict typing mismatch: the SDK types may expect specific Message objects
        from typing import Any, List, cast
        response = client.chat.complete(
            model=MISTRAL_MODEL,
            messages=cast(List[Any], messages)
        )

        # Безопасно извлекаем текст ответа
        reply = None
        if response is not None:
            choices = getattr(response, "choices", None) or (response.get("choices") if isinstance(response, dict) else None)
            if choices:
                first_choice = choices[0]
                message_obj = getattr(first_choice, "message", None) or (first_choice.get("message") if isinstance(first_choice, dict) else None)
                if message_obj:
                    content = getattr(message_obj, "content", None) or (message_obj.get("content") if isinstance(message_obj, dict) else None)
                    if content is not None:
                        reply = content if isinstance(content, str) else str(content)

        if not reply:
            raise RuntimeError("Empty reply from model")
    except Exception as e:
        logger.error(f"Mistral API error: {e}")
        reply = "Извините, произошла техническая ошибка. Попробуйте позже."

    await message.answer(reply)

    # Сохраняем сообщение и ответ в сессию
    profile.setdefault("session_messages", [])
    profile["session_messages"].append({"role": "user", "content": text})
    profile["session_messages"].append({"role": "assistant", "content": reply})

    # Добавляем в векторную память (только сообщение пользователя)
    await add_message_to_vector(user_id, text, role="user")

    # Обновляем этап воронки (очень примитивно: после 2-3 сообщений переключаем)
    # Здесь можно было бы спросить у LLM, но пока так
    state = profile.get("session_state", "gathering_request")
    if state == "gathering_request" and len(profile["session_messages"]) >= 2:
        state = "context"
    elif state == "context" and len(profile["session_messages"]) >= 4:
        state = "feelings"
    elif state == "feelings" and len(profile["session_messages"]) >= 6:
        state = "meanings"
    elif state == "meanings" and len(profile["session_messages"]) >= 8:
        state = "resource"
    profile["session_state"] = state
    await save_user_profile(user_id, profile)

    # Запускаем обновление профиля каждые ANALYZE_EVERY_N сообщений
    if len(profile.get("session_messages", [])) % (2 * ANALYZE_EVERY_N) == 0:
        if callable(update_profile_from_dialog):
            asyncio.create_task(update_profile_from_dialog(user_id))