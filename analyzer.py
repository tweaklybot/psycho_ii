import json
from config import config
from typing import List, Dict, Any

ANALYZER_PROMPT = """
Ты — система извлечения информации из психологических сессий.
Дан диалог между ИИ-психологом и пользователем.
Извлеки новую важную информацию для JSON-профиля пользователя (частичное обновление).
Опирайся на следующие поля:
- current_state: строка, текущее психоэмоциональное состояние, настроение.
- emotional_triggers: список строк, триггеры, вызывающие сильные эмоции.
- coping_strategies: список строк, стратегии совладания, которые использует пользователь.
- resources: список строк, сильные стороны, внешние и внутренние ресурсы.
- therapy_goals: список строк, цели, которые обозначает пользователь (явно или неявно).
- significant_events: список строк, важные события, упомянутые в разговоре.
- other_notes: строка, любые дополнительные наблюдения.

Выведи ТОЛЬКО валидный JSON с обновлёнными полями (только те, что удалось извлечь). Не дополняй несуществующей информацией.
Если никакой новой информации нет, верни пустой объект `{}`.
Диалог:
"""

async def analyze_session(mistral_client: Any, messages: List[Dict]) -> dict:
    """messages: список {"role": "user"/"assistant", "content": str} всей сессии."""
    if not messages:
        return {}
    # Формируем текст диалога
    dialog_text = ""
    for m in messages:
        role = "Пользователь" if m["role"] == "user" else "Психолог"
        dialog_text += f"{role}: {m['content']}\n"

    full_prompt = ANALYZER_PROMPT + dialog_text

    try:
        response = await mistral_client.chat.complete_async(
            model=config.mistral_chat_model,
            messages=[{"role": "system", "content": full_prompt}],
            temperature=0.1,
            max_tokens=800
        )
        answer = response.choices[0].message.content.strip()
        # Извлекаем JSON
        # Иногда Mistral оборачивает JSON в ```json ... ```
        if "```json" in answer:
            answer = answer.split("```json")[1].split("```")[0].strip()
        elif "```" in answer:
            answer = answer.split("```")[1].split("```")[0].strip()
        return json.loads(answer)
    except Exception as e:
        print(f"Анализ сессии провален: {e}")
        return {}