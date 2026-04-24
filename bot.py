import asyncio
import os
import signal
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import config
from memory import Database
from vector_store import VectorMemory
import handlers

# Lightweight web server for Render (free requires a web service)
from aiohttp import web, ClientSession


async def _start_web_server(port: int):
    app = web.Application()

    async def handle_root(request):
        return web.Response(text="OK")

    async def handle_health(request):
        return web.json_response({"status": "ok"})

    app.add_routes([web.get('/', handle_root), web.get('/health', handle_health)])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    return runner


async def _keepalive_loop(target_url: str, interval_sec: int = 300):
    if not target_url:
        print("Keepalive: no EXTERNAL_URL set, skipping keepalive pings")
        return
    print(f"Keepalive: pinging {target_url} every {interval_sec} seconds")
    async with ClientSession() as session:
        while True:
            try:
                async with session.get(target_url, timeout=30) as resp:
                    print(f"Keepalive ping status: {resp.status}")
            except Exception as e:
                print(f"Keepalive ping failed: {e}")
            await asyncio.sleep(interval_sec)


async def main():
    # Инициализация БД
    db = Database(config.database_url)
    await db.init()
    # Инициализация Mistral клиента (если ключ задан)
    mistral = None
    if config.mistral_api_key:
        try:
            mistral = MistralClient(api_key=config.mistral_api_key)
        except Exception as e:
            print("Не удалось инициализировать MistralClient:", e)
            mistral = None

    # Инициализация векторной памяти
    vec_mem = VectorMemory(persist_directory=config.chroma_persist_dir)

    # Передача зависимостей в обработчики
    handlers.setup_handlers(db, mistral, vec_mem)

    # Бот и диспетчер (создаём только если есть токен)
    bot = None
    dp = None
    will_poll = bool(config.bot_token)
    if will_poll:
        bot = Bot(token=config.bot_token, parse_mode="HTML")
        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(handlers.router)
    else:
        print("Warning: BOT_TOKEN not set — Telegram polling will be skipped. Service will still run web server.")

    # Web server settings (Render provides $PORT and user should set EXTERNAL_URL)
    port = int(os.environ.get("PORT", "10000"))
    external_url = os.environ.get("EXTERNAL_URL")

    # Start web server and keepalive (so Render sees a listening web process)
    web_runner = await _start_web_server(port)

    keepalive_task = None
    if external_url:
        keepalive_task = asyncio.create_task(_keepalive_loop(external_url, interval_sec=300))
    else:
        print("Warning: EXTERNAL_URL not set — service will not self-ping. Consider adding external uptime monitor.")

    print("Веб‑сервер запущен...")

    # Запустить polling только если токен задан
    polling_task = None
    if will_poll and dp and bot:
        polling_task = asyncio.create_task(dp.start_polling(bot))

    # Обрабатываем сигналы для корректного завершения
    stop = asyncio.Event()

    def _on_stop(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _on_stop)
        except NotImplementedError:
            pass

    await stop.wait()

    # Cleanup
    if keepalive_task:
        keepalive_task.cancel()
    if polling_task:
        polling_task.cancel()
    if bot:
        try:
            await bot.session.close()
        except Exception:
            pass
    if web_runner:
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())