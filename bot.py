#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Премиум Стикеры и Эмодзи – Telegram бот.
Зависимости:
    pip install python-telegram-bot==20.7 Pillow==10.2.0 python-dotenv

Версия python-telegram-bot: 20+

Настройки хранятся в файле .env (создайте его рядом с bot.py):
    BOT_TOKEN=ваш_токен
    CACHE_CHAT_ID=123456789            # ID чата для кэша инлайн-стикеров (0 чтобы отключить)
    ADMIN_IDS=111111,222222           # ID администраторов через запятую (необязательно)
"""

import asyncio
import io
import logging
import math
import os
import random
from typing import Optional, Tuple

from dotenv import load_dotenv  # pip install python-dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedSticker,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    InlineQueryHandler,
    filters,
    ContextTypes,
)
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageColor

# ----------------------------------------------------------------------
# Загрузка переменных окружения из .env
# ----------------------------------------------------------------------
load_dotenv()

# Обязательные настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в .env файле")

CACHE_CHAT_ID = int(os.getenv("CACHE_CHAT_ID", "0"))
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()] if ADMIN_IDS_STR else []

# Пути к шрифту и рамке (можно тоже вынести в .env при желании)
FONT_PATH = os.getenv("FONT_PATH", "DejaVuSans-Bold.ttf")
FRAME_PATH = os.getenv("FRAME_PATH", "")

# Размеры итогового стикера
STICKER_SIZE = 512
# Цвета для программной рамки
GOLD = (255, 215, 0)
DARK_GOLD = (184, 134, 11)
# ----------------------------------------------------------------------

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Временное хранилище file_id для инлайн-запросов (текст -> file_id)
inline_cache: dict[str, str] = {}

# ----------------------------------------------------------------------
# Вспомогательные функции работы с изображениями
# (без изменений)
# ----------------------------------------------------------------------
def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Возвращает шрифт DejaVuSans-Bold, если файл существует, иначе стандартный."""
    if os.path.exists(FONT_PATH):
        return ImageFont.truetype(FONT_PATH, size=size)
    return ImageFont.load_default(size=size)

def load_frame() -> Optional[Image.Image]:
    if FRAME_PATH and os.path.isfile(FRAME_PATH):
        return Image.open(FRAME_PATH).convert("RGBA")
    return None

def create_golden_frame(size: int) -> Image.Image:
    # ... (код без изменений, как в предыдущем ответе)
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    border = 20
    draw.rectangle([(0, 0), (size - 1, size - 1)], outline=GOLD, width=border)
    draw.rectangle([(border, border), (size - 1 - border, size - 1 - border)], outline=DARK_GOLD, width=4)
    font = get_font(50)
    text = "PREMIUM"
    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    text_x = (size - text_w) // 2
    text_y = border + 10
    draw.text((text_x + 2, text_y + 2), text, fill=(0, 0, 0, 180), font=font)
    draw.text((text_x, text_y), text, fill=GOLD, font=font)
    for _ in range(80):
        x = random.randint(border + 5, size - border - 5)
        y = random.randint(text_y + text_h + 10, size - border - 5)
        radius = random.randint(1, 3)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(255, 255, 255, random.randint(150, 255)))
    for angle in [0, 90, 180, 270]:
        rad = math.radians(angle)
        cx = size // 2 + int((size // 2 - border) * math.cos(rad))
        cy = size // 2 + int((size // 2 - border) * math.sin(rad))
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 255, 200))
    return base

def process_photo(photo: bytes) -> io.BytesIO:
    image = Image.open(io.BytesIO(photo)).convert("RGBA")
    image.thumbnail((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)
    frame = load_frame()
    if frame is None:
        frame = create_golden_frame(STICKER_SIZE)
    if frame.size != (STICKER_SIZE, STICKER_SIZE):
        frame = frame.resize((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)
    final = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE))
    offset_x = (STICKER_SIZE - image.width) // 2
    offset_y = (STICKER_SIZE - image.height) // 2
    final.paste(image, (offset_x, offset_y))
    final = Image.alpha_composite(final, frame)
    output = io.BytesIO()
    final.save(output, format="WEBP", quality=90)
    output.seek(0)
    return output

def create_premium_emoji(text: str) -> io.BytesIO:
    # ... (без изменений)
    size = STICKER_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    radius = size // 2 - 40
    inner_color = (255, 215, 0)
    outer_color = (25, 25, 112)
    for y in range(size):
        for x in range(size):
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            if dist <= radius:
                t = dist / radius
                r = int(inner_color[0] * (1 - t) + outer_color[0] * t)
                g = int(inner_color[1] * (1 - t) + outer_color[1] * t)
                b = int(inner_color[2] * (1 - t) + outer_color[2] * t)
                draw.point((x, y), fill=(r, g, b, 255))
    crown_shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(crown_shadow)
    crown_symbol = "👑"
    crown_font = get_font(120)
    crown_bbox = shadow_draw.textbbox((0, 0), crown_symbol, font=crown_font)
    crown_w = crown_bbox[2] - crown_bbox[0]
    crown_x = cx - crown_w // 2 + 5
    crown_y = cy - radius - 20 + 5
    shadow_draw.text((crown_x, crown_y), crown_symbol, fill=(0, 0, 0, 100), font=crown_font)
    crown_main = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    main_draw = ImageDraw.Draw(crown_main)
    main_draw.text((crown_x - 5, crown_y - 5), crown_symbol, fill=(255, 215, 0, 255), font=crown_font)
    font = get_font(200)
    for try_size in range(200, 50, -10):
        font = get_font(try_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= radius * 1.6 and (bbox[3] - bbox[1]) <= radius * 1.2:
            break
    text_x = cx - (bbox[2] - bbox[0]) // 2
    text_y = cy - (bbox[3] - bbox[1]) // 2 - 10
    text_shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw_text = ImageDraw.Draw(text_shadow)
    shadow_draw_text.text((text_x + 4, text_y + 4), text, fill=(0, 0, 0, 180), font=font)
    text_main = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    main_draw_text = ImageDraw.Draw(text_main)
    main_draw_text.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)
    img = Image.alpha_composite(img, crown_shadow)
    img = Image.alpha_composite(img, crown_main)
    img = Image.alpha_composite(img, text_shadow)
    img = Image.alpha_composite(img, text_main)
    output = io.BytesIO()
    img.save(output, format="WEBP", quality=90)
    output.seek(0)
    return output

# ----------------------------------------------------------------------
# Обработчики команд и кнопок
# ----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("✨ Создать премиум-стикер", callback_data="create_sticker"),
         InlineKeyboardButton("😎 Создать премиум-эмодзи", callback_data="create_emoji")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎉 Добро пожаловать в бот «Премиум Стикеры и Эмодзи»!\n\nВыберите действие:",
        reply_markup=reply_markup,
    )

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    help_text = (
        "📘 <b>Премиум Стикеры и Эмодзи</b>\n\n"
        "• <b>Создать премиум-стикер</b> — отправьте фото, и бот превратит его в стикер с золотой рамкой и надписью PREMIUM.\n"
        "• <b>Создать премиум-эмодзи</b> — отправьте текст (до 5 символов), бот сгенерирует стикер в премиум-оформлении.\n"
        "• <b>Инлайн-режим:</b> в любом чате напишите @PremiumStikerEmojiBot <текст>, чтобы сразу отправить готовый стикер-эмодзи.\n\n"
        "По любым вопросам обращайтесь к администратору."
    )
    await query.message.reply_text(help_text, parse_mode="HTML")

# Диалоги создания стикера и эмодзи (без изменений)
WAIT_PHOTO = 1
async def sticker_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📸 Отправьте мне фотографию для премиум-стикера.")
    return WAIT_PHOTO

async def sticker_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте именно фотографию.")
        return WAIT_PHOTO
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    try:
        sticker_bytes = process_photo(bytes(photo_bytes))
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("⚠️ Не удалось обработать фото.")
        return ConversationHandler.END
    await update.message.reply_sticker(sticker=sticker_bytes)
    await update.message.reply_text("✅ Ваш премиум-стикер готов!")
    return ConversationHandler.END

async def sticker_not_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Ожидалась фотография. Попробуйте снова.")
    return WAIT_PHOTO

WAIT_TEXT = 2
async def emoji_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("✏️ Введите текст (до 5 символов) для премиум-эмодзи.")
    return WAIT_TEXT

async def emoji_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip() if update.message.text else ""
    if len(text) > 5:
        await update.message.reply_text("⚠️ Максимальная длина — 5 символов.")
        return WAIT_TEXT
    if not text:
        await update.message.reply_text("❗️ Текст не может быть пустым.")
        return WAIT_TEXT
    try:
        sticker_bytes = create_premium_emoji(text)
    except Exception as e:
        logger.error(f"Ошибка генерации эмодзи: {e}")
        await update.message.reply_text("⚠️ Не удалось создать эмодзи.")
        return ConversationHandler.END
    await update.message.reply_sticker(sticker=sticker_bytes)
    await update.message.reply_text(f"✅ Премиум-эмодзи «{text}» готов!")
    return ConversationHandler.END

# Инлайн-режим
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CACHE_CHAT_ID:
        return
    query = update.inline_query.query.strip()
    if len(query) > 5:
        query = query[:5]
    if not query:
        return
    sticker_id = inline_cache.get(query)
    if sticker_id is None:
        try:
            sticker_bytes = create_premium_emoji(query)
            msg = await context.bot.send_sticker(chat_id=CACHE_CHAT_ID, sticker=sticker_bytes)
            sticker_id = msg.sticker.file_id
            inline_cache[query] = sticker_id
            await context.bot.delete_message(chat_id=CACHE_CHAT_ID, message_id=msg.message_id)
        except Exception as e:
            logger.error(f"Ошибка инлайн-стикера: {e}")
            return
    result = InlineQueryResultCachedSticker(id=query, sticker_file_id=sticker_id)
    await update.inline_query.answer(results=[result], cache_time=10)

# ----------------------------------------------------------------------
# Главная функция
# ----------------------------------------------------------------------
def main() -> None:
    logger.info("🚀 Запуск бота «Премиум Стикеры и Эмодзи»...")
    logger.info(f"Загружены администраторы: {ADMIN_IDS}")

    application = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler для стикеров
    sticker_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(sticker_start, pattern="^create_sticker$")],
        states={
            WAIT_PHOTO: [
                MessageHandler(filters.PHOTO, sticker_receive_photo),
                MessageHandler(~filters.COMMAND, sticker_not_photo),
            ],
        },
        fallbacks=[],
    )

    # ConversationHandler для эмодзи
    emoji_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(emoji_start, pattern="^create_emoji$")],
        states={
            WAIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_receive_text),
            ],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^help$"))
    application.add_handler(sticker_conv)
    application.add_handler(emoji_conv)

    if CACHE_CHAT_ID:
        application.add_handler(InlineQueryHandler(inline_query))
        logger.info(f"Инлайн-режим включён, кэш-чат ID: {CACHE_CHAT_ID}")
    else:
        logger.warning("Инлайн-режим отключён (CACHE_CHAT_ID=0).")

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()