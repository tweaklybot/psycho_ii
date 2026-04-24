import json
import os

BASE_PROMPT_FILE = os.path.join(os.path.dirname(__file__), 'промт.txt')

def load_base_prompt() -> str:
    with open(BASE_PROMPT_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    # Заменить плейсхолдеры для helpline
    content = content.replace('{helpline_1}', '8-800-2000-122')
    content = content.replace('{helpline_2}', 'местные номера')
    return content

ETHICAL_BASE_PROMPT = load_base_prompt()

def build_system_prompt(profile_json: dict, relevant_history: str, session_state: str) -> str:
    prompt = ETHICAL_BASE_PROMPT.replace('{user_profile}', json.dumps(profile_json, ensure_ascii=False, indent=2))
    prompt += f"\n\n**Текущий этап воронки**: {session_state}\n\n"
    if relevant_history:
        prompt += f"**Похожие прошлые диалоги с этим пользователем**:\n{relevant_history}\n"
    return prompt