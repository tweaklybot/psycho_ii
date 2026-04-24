import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot_data.db")
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma_storage")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "user_memories")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", 10))
ANALYZE_EVERY_N = int(os.getenv("ANALYZE_EVERY_N", 5))
PORT = int(os.getenv("PORT", 8000))