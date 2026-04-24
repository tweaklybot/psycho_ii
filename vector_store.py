import os
from mistralai import Mistral
from chromadb import HttpClient

# Инициализация клиентов
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
client = Mistral(api_key=MISTRAL_API_KEY)

chroma_client = HttpClient(host=os.getenv("CHROMA_HOST", "localhost"), port=int(os.getenv("CHROMA_PORT", 8000)))
collection = chroma_client.get_or_create_collection(name="sticker_messages")

def get_embedding(text: str):
    """Получение эмбеддинга текста через Mistral AI"""
    response = client.embeddings.create(
        model="mistral-embed",
        inputs=[text]
    )
    return response.data[0].embedding

def add_message_to_vector(message_text: str, metadata: dict):
    """Добавление сообщения в векторное хранилище"""
    embedding = get_embedding(message_text)
    collection.add(
        embeddings=[embedding],
        documents=[message_text],
        metadatas=[metadata],
        ids=[metadata.get("user_id") + "_" + str(metadata.get("date", ""))]
    )

def search_similar_messages(query: str, n_results: int = 5):
    """Поиск похожих сообщений"""
    query_embedding = get_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results
    )
    return results
