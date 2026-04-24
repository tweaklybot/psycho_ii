import logging
import config
import mistralai
from typing import TYPE_CHECKING, Any, cast

# Static type checking import for linters like Pylance (no runtime import)
if TYPE_CHECKING:
    import chromadb  # type: ignore
    from chromadb.utils import embedding_functions  # type: ignore

# Try to import chromadb at runtime, but provide an in-memory fallback for environments
try:
    import chromadb  # type: ignore
    from chromadb.utils import embedding_functions  # type: ignore
    _CHROMADB_AVAILABLE = True
except Exception:
    chromadb = None  # type: ignore
    embedding_functions = None  # type: ignore
    _CHROMADB_AVAILABLE = False

logger = logging.getLogger(__name__)

# Инициализация клиента Chroma или in-memory fallback
if _CHROMADB_AVAILABLE:
    # cast chromadb to Any to satisfy type checker about attributes
    chroma_client = cast(Any, chromadb).PersistentClient(path=config.CHROMA_PERSIST_DIR)
    collection = chroma_client.get_or_create_collection(
        name=config.CHROMA_COLLECTION_NAME,
        embedding_function=None  # мы будем передавать эмбеддинги вручную от Mistral
    )
else:
    class _InMemoryCollection:
        def __init__(self):
            self._store = {}

        def add(self, ids, embeddings, metadatas, documents):
            for i, _id in enumerate(ids):
                self._store[_id] = {
                    "embedding": embeddings[i],
                    "metadata": metadatas[i],
                    "document": documents[i]
                }

        def query(self, query_embeddings, where, n_results, include):
            user_id = where.get("user_id")
            docs = [v["document"] for v in self._store.values() if v["metadata"].get("user_id") == user_id]
            return {"ids": [list(self._store.keys())], "documents": [docs]}

        def get(self, where):
            user_id = where.get("user_id")
            ids = [k for k, v in self._store.items() if v["metadata"].get("user_id") == user_id]
            docs = [v["document"] for k, v in self._store.items() if v["metadata"].get("user_id") == user_id]
            return {"ids": [ids], "documents": [docs]}

        def delete(self, ids):
            for _id in ids:
                self._store.pop(_id, None)

    collection = _InMemoryCollection()

def get_embedding(text: str) -> list[float]:
    """Получает эмбеддинг текста через Mistral API."""
    client = mistralai.Mistral(api_key=config.MISTRAL_API_KEY)
    try:
        resp = client.embeddings.create(
            model="mistral-embed",
            inputs=[text]
        )
        # Попробуем безопасно извлечь эмбеддинг
        data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
        if data and len(data) > 0:
            emb = getattr(data[0], "embedding", None) or (data[0].get("embedding") if isinstance(data[0], dict) else None)
            if isinstance(emb, list):
                return emb
        raise RuntimeError("Invalid embedding response")
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        # fallback: нули (некрасиво, но чтобы не падать)
        return [0.0] * 1024  # размерность для mistral-embed

async def add_message_to_vector(user_id: int, text: str, role: str = "user"):
    """Добавляет сообщение в векторную БД."""
    emb = get_embedding(text)
    # Генерируем уникальный ID
    import uuid
    doc_id = f"{user_id}_{uuid.uuid4()}"
    collection.add(
        ids=[doc_id],
        embeddings=[emb],
        metadatas=[{"user_id": str(user_id), "role": role, "text": text[:500]}],
        documents=[text]
    )

async def search_similar_messages(user_id: int, query: str, top_k: int = 3) -> list[str]:
    """Ищет похожие сообщения пользователя из прошлого."""
    emb = get_embedding(query)
    try:
        results = collection.query(
            query_embeddings=[emb],
            where={"user_id": str(user_id)},
            n_results=top_k,
            include=["documents"]
        )

        # Поддерживаем структуру и объект/словарь
        if not results:
            return []

        docs = getattr(results, "documents", None) or (results.get("documents") if isinstance(results, dict) else None)
        if docs and len(docs) > 0:
            return docs[0]
    except Exception as e:
        logger.error(f"Similarity search error: {e}")
    return []

async def delete_user_vectors(user_id: int):
    """Удаляет все векторы пользователя."""
    # Chroma не имеет удобного удаления по метаданным, поэтому через get+delete
    try:
        results = collection.get(where={"user_id": str(user_id)})
        ids_to_delete = results["ids"]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
    except Exception as e:
        logger.error(f"Delete vectors error: {e}")