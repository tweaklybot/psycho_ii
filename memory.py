import aiosqlite
import asyncio
import json
from typing import Optional, Dict, List
from datetime import datetime

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.locks: Dict[int, asyncio.Lock] = {}
        # in-memory хранилище текущих сессий: user_id -> list of {"role": "user"/"assistant", "content": str}
        self.sessions: Dict[int, list] = {}

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    consent BOOLEAN DEFAULT FALSE,
                    session_state TEXT DEFAULT 'запрос',
                    profile_json TEXT DEFAULT '{}'
                )
            """)
            await db.commit()

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self.locks:
            self.locks[user_id] = asyncio.Lock()
        return self.locks[user_id]

    async def create_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,)
            )
            await db.commit()

    async def get_profile(self, user_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT consent, session_state, profile_json FROM users WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                consent, session_state, profile_json = row
                profile = json.loads(profile_json) if profile_json else {}
                return {
                    "consent": bool(consent),
                    "session_state": session_state,
                    "profile": profile
                }

    async def update_consent(self, user_id: int, consent: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET consent=? WHERE user_id=?",
                (consent, user_id)
            )
            await db.commit()

    async def update_session_state(self, user_id: int, state: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET session_state=? WHERE user_id=?",
                (state, user_id)
            )
            await db.commit()

    async def update_profile(self, user_id: int, profile_update: dict) -> None:
        """Слияние нового частичного профиля с существующим (с локом)."""
        lock = self._get_lock(user_id)
        async with lock:
            current = await self.get_profile(user_id)
            if current is None:
                await self.create_user(user_id)
                current = {"consent": False, "session_state": "запрос", "profile": {}}
            merged_profile = {**current["profile"], **profile_update}
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE users SET profile_json=? WHERE user_id=?",
                    (json.dumps(merged_profile, ensure_ascii=False), user_id)
                )
                await db.commit()

    async def delete_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            await db.commit()
        # Очистить in-memory данные
        self.sessions.pop(user_id, None)
        self.locks.pop(user_id, None)

    # Методы для управления сессией в памяти
    def add_session_message(self, user_id: int, role: str, content: str):
        if user_id not in self.sessions:
            self.sessions[user_id] = []
        self.sessions[user_id].append({"role": role, "content": content, "timestamp": datetime.now().isoformat()})

    def get_session_messages(self, user_id: int) -> list:
        return self.sessions.get(user_id, [])

    def clear_session(self, user_id: int):
        self.sessions[user_id] = []

    def count_user_messages_in_session(self, user_id: int) -> int:
        """Кол-во сообщений пользователя в текущей сессии."""
        msgs = self.sessions.get(user_id, [])
        return sum(1 for m in msgs if m["role"] == "user")