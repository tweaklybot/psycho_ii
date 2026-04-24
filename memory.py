import aiosqlite
import json
import config

async def get_db():
    # Упрощённо: подключаемся каждый раз, для демо
    db = await aiosqlite.connect(config.DATABASE_URL.split("///")[1] if "sqlite" in config.DATABASE_URL else config.DATABASE_URL)
    await db.execute("CREATE TABLE IF NOT EXISTS profiles (user_id INTEGER PRIMARY KEY, profile TEXT)")
    return db

async def get_user_profile(user_id: int) -> dict:
    db = await get_db()
    async with db.execute("SELECT profile FROM profiles WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        if row:
            return json.loads(row[0])
    await db.close()
    # Возвращаем базовый профиль
    return {
        "agreed": False,
        "session_state": "gathering_request",
        "session_messages": [],
        "current_request": "",
        "emotional_triggers": [],
        "coping_strategies": [],
        "resources": [],
        "therapy_goals": [],
        "preferred_tone": "warm",
        "avoid_phrases": []
    }

async def save_user_profile(user_id: int, profile: dict):
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO profiles (user_id, profile) VALUES (?, ?)",
        (user_id, json.dumps(profile, ensure_ascii=False))
    )
    await db.commit()
    await db.close()

async def delete_user_data(user_id: int):
    db = await get_db()
    await db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
    await db.commit()
    await db.close()
    # Также удалим векторные данные (по возможности)
    from vector_store import delete_user_vectors
    await delete_user_vectors(user_id)