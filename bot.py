import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, LOG_LEVEL, PORT
from handlers import router

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Запускаем веб-сервер aiohttp
    app = web.Application()

    # Эндпоинт для проверки здоровья (обязательно для Render)
    async def health_check(request):
        return web.Response(text="OK")

    app.router.add_get("/", health_check)

    # Запускаем polling бота в фоне
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot))

    # Стартуем веб-сервер на порту из Render
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

    # Держим бесконечно
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())