import logging
from memory import get_user_profile, save_user_profile
import json
import config
from mistralai import MistralClient

logger = logging.getLogger(__name__)

async def update_profile_from_dialog(user_id: int):
    """Анализирует текущую сессию и обновляет профиль пользователя."""
    profile = await get_user_profile(user_id)
    session_msgs = profile.get("session_messages", [])
    if not session_msgs:
        return

    # Формируем текстовый диалог
    dialog_text = "\n".join([f"{m['role']}: {m['content']}" for m in session_msgs[-20:]])  # последние 20 сообщений

    client = MistralClient(api_key=config.MISTRAL_API_KEY)
    instruction = (
        "Проанализируй следующий диалог между ИИ-психологом и пользователем. "
        "Извлеки новую информацию, которая важна для понимания пользователя: "
        "- изменилось ли настроение или появились новые жалобы (поле current_request), "
        "- новые эмоциональные триггеры (emotional_triggers), "
        "- новые стратегии совладания (coping_strategies) или ресурсы (resources), "
        "- новые цели терапии (therapy_goals), "
        "- какие фразы лучше избегать (avoid_phrases) и какой тон предпочтителен (preferred_tone). "
        "Выведи ТОЛЬКО валидный JSON с полями, которые нужно обновить/добавить (частичный апдейт). "
        "Пример: {\"current_request\": \"боится собеседований\", \"emotional_triggers\": [\"звонок начальника\"]}. "
        "Никакого текста вне JSON."
    )
    try:
        response = client.chat(
            model=config.MISTRAL_MODEL,
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": dialog_text}
            ],
            response_format={"type": "json_object"}  # если модель поддерживает
        )

        # Безопасно извлекаем текст из ответа — может быть None или иметь разную структуру
        if response is None:
            logger.error("Empty response from Mistral")
            return

        choices = getattr(response, "choices", None) or (response.get("choices") if isinstance(response, dict) else None)
        if not choices:
            logger.error("No choices in Mistral response")
            return

        first_choice = choices[0]
        message_obj = getattr(first_choice, "message", None) or (first_choice.get("message") if isinstance(first_choice, dict) else None)
        if not message_obj:
            logger.error("No message object in first choice")
            return

        content = getattr(message_obj, "content", None) or (message_obj.get("content") if isinstance(message_obj, dict) else None)
        if content is None:
            logger.error("No content in message object")
            return

        # content может быть нестрокой — привести к строке перед парсингом
        result_text = content if isinstance(content, str) else str(content)
        updates = json.loads(result_text)

        # Мерджим с профилем
        for key, value in updates.items():
            if isinstance(value, list) and key in profile and isinstance(profile[key], list):
                profile[key] = list(set(profile[key] + value))
            else:
                profile[key] = value

        await save_user_profile(user_id, profile)
        logger.info(f"Profile updated for user {user_id}: {updates}")

    except Exception as e:
        logger.error(f"Profile update failed: {e}")