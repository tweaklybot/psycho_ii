import aiosqlite
import asyncio
import json
from typing import List, Tuple
import numpy as np
import time


class VectorMemory:
    """Лёгкая реализация векторной памяти на SQLite + NumPy.

    Таблица `embeddings` содержит: id TEXT PRIMARY KEY, user_id INTEGER,
    embedding TEXT(JSON), user_message TEXT, bot_response TEXT, timestamp TEXT
    """

    def __init__(self, persist_directory: str = "./chroma_db"):
        # Путь к sqlite файлу внутри каталога persist_directory
        self.db_path = f"{persist_directory.rstrip('/')}/vectors.db"

    async def _ensure_table(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    embedding TEXT,
                    user_message TEXT,
                    bot_response TEXT,
                    timestamp TEXT
                )
                """
            )
            await db.commit()

    async def add_memory(self, user_id: int, user_message: str, bot_response: str, embedding: List[float]):
        await self._ensure_table()
        rec_id = f"{user_id}_{time.time()}"
        emb_json = json.dumps(embedding)
        ts = str(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO embeddings (id, user_id, embedding, user_message, bot_response, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (rec_id, user_id, emb_json, user_message, bot_response, ts)
            )
            await db.commit()

    async def search_similar(self, user_id: int, query_embedding: List[float], top_k: int = 3) -> List[Tuple[str, str]]:
        await self._ensure_table()

        # Получаем все embedding для пользователя
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_message, bot_response, embedding FROM embeddings WHERE user_id=?", (user_id,)) as cur:
                rows = await cur.fetchall()

        if not rows:
            return []

        # Вычисляем косинусное сходство в thread (NumPy)
        def compute():
            embs = []
            docs = []
            bots = []
            for user_msg, bot_resp, emb_json in rows:
                try:
                    vec = np.array(json.loads(emb_json), dtype=float)
                    embs.append(vec)
                    docs.append(user_msg)
                    bots.append(bot_resp)
                except Exception:
                    continue
            if not embs:
                return []
            embs = np.vstack(embs)
            q = np.array(query_embedding, dtype=float)
            # Normalize
            embs_norm = embs / np.linalg.norm(embs, axis=1, keepdims=True)
            q_norm = q / np.linalg.norm(q)
            sims = embs_norm.dot(q_norm)
            top_idx = np.argsort(-sims)[:top_k]
            results = [(docs[i], bots[i]) for i in top_idx]
            return results

        results = await asyncio.to_thread(compute)
        return results

    async def delete_user_memories(self, user_id: int):
        await self._ensure_table()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM embeddings WHERE user_id=?", (user_id,))
            await db.commit()