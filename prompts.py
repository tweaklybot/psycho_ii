import os
from typing import List


def build_system_prompt(profile: dict, similar_docs: List[str]) -> str:
    """Load the base prompt from `promt.txt` and merge it with dynamic profile and similar documents.

    Behavior:
    - If `promt.txt` contains a `{user_profile}` placeholder, it will be replaced with a generated profile block.
    - `{session_state}` and `{similar_context}` placeholders will be replaced when present; otherwise the
      corresponding sections are appended.
    """
    base_path = os.path.join(os.path.dirname(__file__), "promt.txt")
    try:
        with open(base_path, "r", encoding="utf-8") as f:
            base = f.read()
    except Exception:
        base = ""

    # Build a concise user profile block to inject into the base prompt
    memory = "[ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ]\n"
    memory += f"- Текущий запрос: {profile.get('current_request', 'не известен')}\n"
    memory += f"- Состояние сессии: {profile.get('session_state', 'gathering_request')}\n"
    memory += f"- Триггеры: {', '.join(profile.get('emotional_triggers', [])) or 'не известны'}\n"
    memory += f"- Ресурсы: {', '.join(profile.get('resources', [])) or 'не известны'}\n"
    memory += f"- Цели терапии: {', '.join(profile.get('therapy_goals', [])) or 'не указаны'}\n"
    memory += f"- Фразы, которых избегать: {', '.join(profile.get('avoid_phrases', [])) or 'нет'}\n"
    memory += f"- Предпочтительный тон: {profile.get('preferred_tone', 'тёплый, эмпатичный')}\n"
    memory += "[КОНЕЦ ПРОФИЛЯ]\n"

    similar_context = "\n".join(similar_docs) if similar_docs else "Нет релевантных воспоминаний."

    # Inject or append the user profile
    if "{user_profile}" in base:
        final = base.replace("{user_profile}", memory)
    else:
        final = (base + "\n\n" + memory) if base else memory

    # Replace session_state placeholder if present
    final = final.replace("{session_state}", profile.get("session_state", "gathering_request"))

    # Inject or append similar context
    if "{similar_context}" in final:
        final = final.replace("{similar_context}", similar_context)
    else:
        final = final + "\n\n**Похожие прошлые ситуации (из памяти):**\n" + similar_context

    return final