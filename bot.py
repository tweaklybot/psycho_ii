import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from config import BOT_TOKEN, LOG_LEVEL, PORT, WEBHOOK_URL
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

    async def webhook_handler(request):
        data = await request.json()
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="OK")

    app.router.add_get("/", health_check)
    app.router.add_post("/webhook", webhook_handler)

    # Устанавливаем вебхук
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")
    else:
        logger.warning("WEBHOOK_URL not set, webhook not configured")

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