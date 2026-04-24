# ИИ-психолог Telegram Bot

Асинхронный Telegram-бот на Python, использующий Mistral AI для психологических бесед с долговременной векторной памятью и профилем пользователя.

## Возможности


## Установка и запуск

1. Клонируйте репозиторий:
   ```bash
   git clone <url> && cd psychobot
   ```
# ИИ-психолог Telegram Bot

Асинхронный Telegram-бот на Python, использующий Mistral AI для психологических бесед с долговременной векторной памятью и профилем пользователя.

## Краткая инструкция (локально и Render free)

Требования:
- Python 3.12
- ffmpeg (для обработки голосовых сообщений)

Установка зависимостей:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Переменные окружения (создайте `.env`):

```
BOT_TOKEN=...
MISTRAL_API_KEY=...
DATABASE_URL=psychobot.db
CHROMA_PERSIST_DIR=./chroma_db
```

Запуск локально:

```bash
python bot.py
```

Развёртывание на Render (free):
- Render free поддерживает только Web Services для бесплатных планов — мы запускаем веб-процесс, который держит контейнер «живым» и параллельно запускает бота.
- Укажите `python bot.py` как команду запуска или используйте `Procfile` (в этом репозитории `Procfile` запускает `web: python bot.py`).
- Добавьте переменные окружения в Settings → Environment.

Обязательно задайте переменную `EXTERNAL_URL` равную публичному URL вашего сервиса (например `https://your-service.onrender.com`).
Когда `EXTERNAL_URL` задана, бот будет периодически (каждые 5 минут) посылать GET на этот URL, чтобы уменьшить риск простоя на free тарифе.

Примечания и рекомендации:
- Whisper модель не загружается при старте, она загружается лениво при первом голосовом сообщении, чтобы экономить память.
- ChromaDB и некоторые нативные зависимости могут требовать сборки; если на Render возникают сложности, рассмотрите внешнее хранение эмбеддингов или упрощение памяти.
- Тестируйте с бесплатным Mistral-ключом и следите за пределами запросов.

Если хотите, могу:
- помочь обновить `requirements.txt` под легче собираемые зависимости для Render,
- настроить Dockerfile для переносимости,
- добавить тестовый сценарий для локального теста.

## Лёгкий образ (без Whisper / без Chromadb)

Если хочется минимизировать зависимости и размер образа, используйте `requirements-lite.txt` и `Dockerfile-lite`.

Сборка и запуск локально (lite):

```bash
docker build -f Dockerfile-lite -t psychobot:lite .
docker run --env-file .env psychobot:lite
```

Файлы:
- `requirements-lite.txt` — минимальные зависимости (без Whisper и Chromadb).
- `Dockerfile-lite` — образ, использующий `requirements-lite.txt`.

Если хотите, могу собрать этот Docker-образ здесь и прогнать быструю проверку.

---

Авторы: Ваш проект

**Render Server Settings**

- **Service Type:** Web Service — use a web process so Render keeps the container running.
- **Start Command:** `python bot.py` or rely on `Procfile` (`web: python bot.py`).
- **Build Command (optional):** `pip install -r requirements-lite.txt` (use `Dockerfile-lite` for Docker builds).
- **PORT:** Render provides `$PORT`; the app binds to it automatically. No manual port needed.

- **Required Environment Variables:**
   - **BOT_TOKEN:** Telegram bot token (secret).
   - **MISTRAL_API_KEY:** Mistral API key (secret).
   - **DATABASE_URL:** For production, use Postgres URL (e.g. `postgres://user:pass@host:5432/dbname`). For quick dev you can use `./psychobot.db` (SQLite) but note data may be lost on restart.
   - **CHROMA_PERSIST_DIR:** local path for vector DB (optional; used only for local persistence).
   - **EXTERNAL_URL:** public URL of your Render service (e.g. `https://your-service.onrender.com`) — used for self-pinging keepalive.
   - **MISTRAL_CHAT_MODEL / MISTRAL_EMBED_MODEL:** optional overrides for model names.

- **Keepalive / Prevent Idling:**
   - Set `EXTERNAL_URL` to your public service URL. The app will periodically GET this URL every 5 minutes to reduce idling on Render free.
   - Alternatively (or additionally), configure an external uptime monitor (UptimeRobot / Cron-job.org) to ping your root URL every 4–5 minutes.

- **Persistence & Data Safety:**
   - Render free filesystem is ephemeral. SQLite or local files (`vectors.db`, `psychobot.db`, `chroma_db/`) can be lost after redeploys.
   - Recommended production setup: attach a managed Postgres (Render Postgres) and set `DATABASE_URL` accordingly; store embeddings in Postgres or external object store (S3) for durability.

- **Resources Recommendation:**
   - Free tier (small RAM) may work for minimal bot usage but can OOM when heavy tasks run (audio transcription or large numpy ops).
   - Prefer at least 1 vCPU and 1–2 GB RAM for stability. If you see crashes, upgrade the instance.

- **Health Check & Logs:**
   - Enable Render health checks on `/health` (the app serves `/health`).
   - Keep logs enabled in Render; monitor for OOM / repeated restarts.

- **Secrets & Security:**
   - Mark `BOT_TOKEN` and `MISTRAL_API_KEY` as secret in Render's Environment settings.
   - Do not commit `.env` to the repository.

- **Backups (if using SQLite local storage):**
   - Periodically copy `vectors.db` and `psychobot.db` to external storage (S3) using a scheduled job or manual download.
   - Better: migrate to Postgres for built-in backups.

- **Quick Render Setup Steps:**
   1. Push repository to GitHub (or connect repo in Render).
 2. Create a new **Web Service** in Render linked to the repo.
 3. Choose Python environment and set Build Command: `pip install -r requirements-lite.txt` (or use Dockerfile-lite).
 4. Set Start Command: `python bot.py` (or rely on `Procfile`).
 5. Add environment variables in Settings → Environment (see list above). Mark secrets.
 6. Deploy and note the public service URL. Set this URL as `EXTERNAL_URL` in Environment.
 7. Verify the service: open `https://<your-service>/health` and ensure JSON `{"status":"ok"}`.

- **Optional:** If you prefer not to self-ping from the app, configure an external monitor to call `https://<your-service>/` every 4 minutes.

If хотите, я могу сгенерировать готовый список переменных для вставки в Render UI, или подготовить миграцию `DATABASE_URL` → Postgres. Скажите, что делаем дальше.
