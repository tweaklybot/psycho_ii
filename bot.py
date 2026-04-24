#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Бот «Премиум Стикеры и Эмодзи»
Генерирует статичные и анимированные (TGS) стикеры в премиум-стиле Telegram.

Зависимости:
    pip install "python-telegram-bot>=20.0,<21" Pillow

Для анимированных стикеров не требуется дополнительных библиотек – TGS создаётся вручную.
"""

import asyncio
import gzip
import io
import json
import logging
import math
import os
import random
import time
from typing import Optional, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageOps
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultCachedSticker,
    InputSticker,
    Sticker,
)
from telegram.constants import ParseMode
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
from telegram.error import BadRequest, TelegramError

# ----------------------------------------------------------------------
# Настройки
# ----------------------------------------------------------------------
# Токен бота – обязательно укажите свой
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

# ID чата для кэширования инлайн-стикеров (бот должен иметь туда доступ)
CACHE_CHAT_ID = int(os.getenv("CACHE_CHAT_ID", "0"))

# Список администраторов (через запятую)
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

# Параметры визуального стиля
GOLD = (255, 215, 0)
DARK_GOLD = (184, 134, 11)
DEEP_BLUE = (25, 25, 112)
STICKER_SIZE = 512  # пикселей

# Параметры анимации (Lottie)
FPS = 30
ANIMATION_DURATION = 3  # секунды

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Состояния ConversationHandler
# ----------------------------------------------------------------------
TEXT, FORMAT, PHOTO, SET_TITLE, SET_TITLE_CONFIRM = range(5)

# ----------------------------------------------------------------------
# Вспомогательные функции изображений
# ----------------------------------------------------------------------
def get_font(size: int) -> ImageFont.FreeTypeFont:
    """Пытается загрузить DejaVuSans-Bold, иначе стандартный."""
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default(size=size)


def create_golden_emoji_static(text: str) -> io.BytesIO:
    """
    Рисует статичное премиум-эмодзи: золотой круг, текст, корона, тень.
    Возвращает PNG в BytesIO.
    """
    size = STICKER_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    radius = size // 2 - 40

    # Градиентный круг
    for y in range(size):
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist <= radius:
                t = dist / radius
                r = int(GOLD[0] * (1 - t) + DEEP_BLUE[0] * t)
                g = int(GOLD[1] * (1 - t) + DEEP_BLUE[1] * t)
                b = int(GOLD[2] * (1 - t) + DEEP_BLUE[2] * t)
                draw.point((x, y), fill=(r, g, b, 255))

    # Корона с тенью
    crown = "👑"
    font_crown = get_font(100)
    bc = draw.textbbox((0, 0), crown, font=font_crown)
    cw, ch = bc[2] - bc[0], bc[3] - bc[1]
    crown_x = cx - cw // 2
    crown_y = cy - radius - 20
    # тень
    draw.text((crown_x + 3, crown_y + 3), crown, fill=(0, 0, 0, 120), font=font_crown)
    draw.text((crown_x, crown_y), crown, fill=GOLD, font=font_crown)

    # Текст с тенью
    font_text = get_font(180)
    while True:
        bbox = draw.textbbox((0, 0), text, font=font_text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= radius * 1.6 and th <= radius * 1.2:
            break
        font_text = get_font(font_text.size - 10)
        if font_text.size <= 50:
            break
    text_x = cx - tw // 2
    text_y = cy - th // 2 - 10
    draw.text((text_x + 3, text_y + 3), text, fill=(0, 0, 0, 150), font=font_text)
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font_text)

    # Блёстки
    for _ in range(50):
        angle = random.uniform(0, 2 * math.pi)
        r = random.uniform(radius * 0.8, radius)
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        spark_size = random.randint(1, 3)
        draw.ellipse(
            (x - spark_size, y - spark_size, x + spark_size, y + spark_size),
            fill=(255, 255, 255, random.randint(150, 255)),
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def create_photo_static(photo_bytes: bytes) -> io.BytesIO:
    """Преобразует фото в премиум-стикер: рамка, свечение, статика PNG."""
    img = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    # Кадрируем в квадрат (центральная обрезка)
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)

    # Создаём рамку
    border = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(border)
    frame_width = 20
    draw.rectangle(
        [frame_width, frame_width, STICKER_SIZE - frame_width, STICKER_SIZE - frame_width],
        outline=GOLD,
        width=frame_width,
    )
    # Внутренняя тонкая рамка
    draw.rectangle(
        [frame_width + 5, frame_width + 5, STICKER_SIZE - frame_width - 5, STICKER_SIZE - frame_width - 5],
        outline=DARK_GOLD,
        width=3,
    )
    # Свечение по углам
    for x, y in [(0, 0), (STICKER_SIZE, 0), (0, STICKER_SIZE), (STICKER_SIZE, STICKER_SIZE)]:
        glow = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.ellipse(
            (x - 60, y - 60, x + 60, y + 60),
            fill=(255, 215, 0, 60),
        )
        border = Image.alpha_composite(border, glow)

    # Наложение рамки на фото
    final = Image.alpha_composite(img, border)

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ----------------------------------------------------------------------
# Генерация анимированных TGS (Lottie) вручную
# ----------------------------------------------------------------------
def _create_lottie_emoji_animation(text: str) -> Dict:
    """Создаёт Lottie-анимацию для премиум-эмодзи."""
    frames = FPS * ANIMATION_DURATION
    layer_common = {
        "ty": 4, "shapes": [], "ip": 0, "op": frames, "st": 0, "nm": "emoji"
    }

    # Базовые параметры
    circle_radius = 200
    center = 256

    # Анимированный градиентный круг (пульсация радиуса)
    circle_shape = {
        "ty": "el",
        "p": {"a": 1, "k": [
            {"t": 0, "s": [center, center]},
            {"t": frames//2, "s": [center, center]},
            {"t": frames, "s": [center, center]}
        ]},
        "s": {"a": 1, "k": [
            {"t": 0, "s": [circle_radius, circle_radius]},
            {"t": frames//2, "s": [circle_radius*1.05, circle_radius*1.05]},
            {"t": frames, "s": [circle_radius, circle_radius]}
        ]},
        "d": 1,
    }

    # Цвет круга (градиент не так просто, используем сплошной цвет и анимируем opacity)
    # Для простоты сделаем два слоя: фон с золотым градиентом (статичный) и пульсирующий белый оверлей.
    # В lottie можно использовать radial gradient, но мы упростим.
    layers = []

    # Фоновый статичный круг (золотой)
    static_bg = {
        "ty": 4,
        "shapes": [
            {
                "ty": "el",
                "p": {"a": 0, "k": [center, center]},
                "s": {"a": 0, "k": [circle_radius, circle_radius]},
                "fl": {"a": 0, "k": [1, 0.84, 0, 1]},  # gold
            }
        ],
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(static_bg)

    # Пульсирующий белый оверлей для эффекта сияния
    pulse_white = {
        "ty": 4,
        "shapes": [
            {
                "ty": "el",
                "p": {"a": 0, "k": [center, center]},
                "s": {"a": 0, "k": [circle_radius*0.8, circle_radius*0.8]},
                "o": {"a": 1, "k": [
                    {"t": 0, "s": [30]},
                    {"t": frames//2, "s": [80]},
                    {"t": frames, "s": [30]}
                ]},
                "fl": {"a": 0, "k": [1, 1, 1, 1]},
            }
        ],
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(pulse_white)

    # Корона (вращается)
    crown_text = "👑"
    crown_layer = {
        "ty": 5,  # text
        "t": {
            "d": {"k": [{"s": {"t": crown_text}}]},
            "p": {"a": 0, "k": [center, center-140]},
            "s": {"a": 0, "k": [80]},
            "r": {"a": 1, "k": [
                {"t": 0, "s": [-10]},
                {"t": frames//2, "s": [10]},
                {"t": frames, "s": [-10]}
            ]},
            "c": {"a": 0, "k": [1, 0.84, 0, 1]},
        },
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(crown_layer)

    # Текст эмодзи (пульсирует прозрачность)
    text_layer = {
        "ty": 5,
        "t": {
            "d": {"k": [{"s": {"t": text}}]},
            "p": {"a": 0, "k": [center, center+20]},
            "s": {"a": 0, "k": [100]},
            "o": {"a": 1, "k": [
                {"t": 0, "s": [100]},
                {"t": frames//2, "s": [70]},
                {"t": frames, "s": [100]}
            ]},
            "c": {"a": 0, "k": [1, 1, 1, 1]},
        },
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(text_layer)

    # Блёстки (маленькие круги, мигающие)
    for i in range(15):
        angle = random.uniform(0, 2 * math.pi)
        r = random.uniform(150, 200)
        x = center + r * math.cos(angle)
        y = center + r * math.sin(angle)
        spark_layer = {
            "ty": 4,
            "shapes": [
                {
                    "ty": "el",
                    "p": {"a": 0, "k": [x, y]},
                    "s": {"a": 0, "k": [4, 4]},
                    "o": {"a": 1, "k": [
                        {"t": 0, "s": [0]},
                        {"t": random.randint(10, frames-10), "s": [100]},
                        {"t": frames, "s": [0]}
                    ]},
                    "fl": {"a": 0, "k": [1, 1, 1, 1]},
                }
            ],
            "ip": 0, "op": frames, "st": 0
        }
        layers.append(spark_layer)

    animation = {
        "v": "5.5.2",
        "fr": FPS,
        "ip": 0,
        "op": frames,
        "w": STICKER_SIZE,
        "h": STICKER_SIZE,
        "nm": "premium_emoji",
        "layers": layers
    }
    return animation


def _create_lottie_photo_animation(photo_bytes: bytes) -> Dict:
    """
    Генерирует анимированный стикер на основе фото:
    медленное увеличение/пульсация и блёстки.
    Фото вставляется как статический слой (закодирован в base64).
    Но для простоты – анимация без фото (рамка и частицы), т.к. вставка фото в Lottie сложна.
    Вместо этого возвращаем статику + анимированную рамку.
    Для реального использования нужно было бы вставить base64-изображение,
    но это возможно через "image" assets. Здесь упрощённый вариант.
    """
    # Упрощённая анимация: золотая рамка, пульсация и блёстки
    frames = FPS * ANIMATION_DURATION
    center = STICKER_SIZE // 2
    size = STICKER_SIZE

    layers = []
    # рамка статичная
    frame_rect = {
        "ty": 4,
        "shapes": [
            {
                "ty": "rc",
                "p": {"a": 0, "k": [center, center]},
                "s": {"a": 0, "k": [size-40, size-40]},
                "r": {"a": 0, "k": 0},
                "stroke": {"a": 0, "k": [1, 0.84, 0, 1]},
                "strokeWidth": {"a": 0, "k": 20},
                "fill": {"a": 0, "k": [0, 0, 0, 0]},
            }
        ],
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(frame_rect)

    # Внутреннее свечение
    glow = {
        "ty": 4,
        "shapes": [
            {
                "ty": "el",
                "p": {"a": 0, "k": [center, center]},
                "s": {"a": 0, "k": [size-80, size-80]},
                "o": {"a": 1, "k": [
                    {"t": 0, "s": [20]},
                    {"t": frames//2, "s": [50]},
                    {"t": frames, "s": [20]}
                ]},
                "fl": {"a": 0, "k": [1, 0.84, 0, 1]},
            }
        ],
        "ip": 0, "op": frames, "st": 0
    }
    layers.append(glow)

    # Блёстки
    for _ in range(10):
        x = random.randint(40, size-40)
        y = random.randint(40, size-40)
        spark = {
            "ty": 4,
            "shapes": [
                {
                    "ty": "el",
                    "p": {"a": 0, "k": [x, y]},
                    "s": {"a": 0, "k": [4, 4]},
                    "o": {"a": 1, "k": [
                        {"t": 0, "s": [0]},
                        {"t": random.randint(10, frames-10), "s": [100]},
                        {"t": frames, "s": [0]}
                    ]},
                    "fl": {"a": 0, "k": [1, 1, 1, 1]},
                }
            ],
            "ip": 0, "op": frames, "st": 0
        }
        layers.append(spark)

    return {
        "v": "5.5.2",
        "fr": FPS,
        "ip": 0,
        "op": frames,
        "w": size,
        "h": size,
        "nm": "premium_sticker",
        "layers": layers
    }


def tgs_from_lottie(lottie_dict: Dict) -> io.BytesIO:
    """Упаковывает Lottie JSON в gzip, возвращает BytesIO."""
    json_str = json.dumps(lottie_dict)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(json_str.encode("utf-8"))
    buf.seek(0)
    return buf


# ----------------------------------------------------------------------
# Генераторы конечных стикеров
# ----------------------------------------------------------------------
def generate_emoji_sticker(text: str, animated: bool) -> io.BytesIO:
    if animated:
        lottie = _create_lottie_emoji_animation(text)
        return tgs_from_lottie(lottie)
    else:
        return create_golden_emoji_static(text)


def generate_photo_sticker(photo_bytes: bytes, animated: bool) -> io.BytesIO:
    if animated:
        lottie = _create_lottie_photo_animation(photo_bytes)
        return tgs_from_lottie(lottie)
    else:
        return create_photo_static(photo_bytes)


# ----------------------------------------------------------------------
# Стикерпак: проверка наличия и создание
# ----------------------------------------------------------------------
async def get_user_sticker_set(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Optional[str]:
    """Возвращает имя набора стикеров пользователя или None."""
    return context.user_data.get("sticker_set")

async def create_sticker_set_for_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE, title: str, first_sticker_bytes: io.BytesIO, emoji: str, animated: bool
) -> Optional[str]:
    """Создаёт новый стикерпак для пользователя и добавляет первый стикер."""
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    # Генерируем имя набора: prems_<user_id>_by_<bot_username>
    set_name = f"prems_{user_id}_by_{bot_username}"
    set_name = set_name[:64]  # ограничение длины

    try:
        # Готовим InputSticker
        sticker_format = "animated" if animated else "static"
        input_sticker = InputSticker(
            sticker=first_sticker_bytes,
            emoji_list=[emoji],
            format=sticker_format,
        )
        await context.bot.create_new_sticker_set(
            user_id=user_id,
            name=set_name,
            title=title,
            stickers=[input_sticker],
            sticker_format=sticker_format,
        )
        context.user_data["sticker_set"] = set_name
        return set_name
    except BadRequest as e:
        logger.error(f"Create sticker set error: {e}")
        if "STICKERSET_INVALID" in str(e):
            await update.message.reply_text("Не удалось создать набор. Попробуйте другое имя.")
        else:
            await update.message.reply_text(f"Ошибка при создании набора: {e}")
    except Exception as e:
        logger.error(f"Unexpected error creating sticker set: {e}")
        await update.message.reply_text("Не удалось создать набор. Попробуйте позже.")
    return None

async def add_sticker_to_set(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, set_name: str, sticker_bytes: io.BytesIO, emoji: str, animated: bool
) -> bool:
    """Добавляет стикер в существующий набор."""
    try:
        sticker_format = "animated" if animated else "static"
        input_sticker = InputSticker(
            sticker=sticker_bytes,
            emoji_list=[emoji],
            format=sticker_format,
        )
        await context.bot.add_sticker_to_set(
            user_id=user_id,
            name=set_name,
            sticker=input_sticker,
        )
        return True
    except BadRequest as e:
        logger.error(f"Add sticker error: {e}")
        if "STICKERSET_INVALID" in str(e):
            return False
        if "STICKERSET_NOT_MODIFIED" in str(e):
            return True  # уже есть
    return False

# ----------------------------------------------------------------------
# Обработчики команд
# ----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и меню."""
    keyboard = [
        [InlineKeyboardButton("✨ Создать премиум-эмодзи", callback_data="create_emoji")],
        [InlineKeyboardButton("🎨 Создать премиум-стикер", callback_data="create_sticker")],
        [InlineKeyboardButton("📦 Мой набор стикеров", callback_data="my_set")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎉 Добро пожаловать в «Премиум Стикеры и Эмодзи»!\n"
        "Выберите действие:",
        reply_markup=reply_markup,
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка."""
    text = (
        "📘 <b>Премиум Стикеры и Эмодзи</b>\n\n"
        "• <b>Создать премиум-эмодзи</b> — введите текст до 5 символов, выберите формат (статичный/анимированный), получите стикер.\n"
        "• <b>Создать премиум-стикер</b> — отправьте фото, выберите формат, получите стикер с золотой рамкой и эффектами.\n"
        "• <b>Мой набор</b> — управление личным стикерпаком.\n"
        "• <b>Инлайн-режим:</b> в любом чате напишите @PremiumStikerEmojiBot текст, чтобы сразу отправить стикер-эмодзи.\n\n"
        "По вопросам: @your_support"
    )
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)

# ----------------------------------------------------------------------
# Диалог создания эмодзи
# ----------------------------------------------------------------------
async def emoji_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("✏️ Введите текст для эмодзи (до 5 символов):")
    return TEXT

async def emoji_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    if len(text) > 5:
        await update.message.reply_text("⚠️ Максимум 5 символов. Попробуйте снова.")
        return TEXT
    if not text:
        await update.message.reply_text("❗️ Текст не может быть пустым.")
        return TEXT
    context.user_data["emoji_text"] = text
    # Предлагаем выбрать формат
    keyboard = [
        [InlineKeyboardButton("🖼 Статичный", callback_data="format_static"),
         InlineKeyboardButton("✨ Анимированный", callback_data="format_animated")]
    ]
    await update.message.reply_text("Выберите тип стикера:", reply_markup=InlineKeyboardMarkup(keyboard))
    return FORMAT

async def emoji_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    animated = query.data == "format_animated"
    text = context.user_data.get("emoji_text", "❓")
    try:
        sticker = generate_emoji_sticker(text, animated)
    except Exception as e:
        logger.error(f"Generate emoji error: {e}")
        await query.message.reply_text("⚠️ Ошибка генерации. Попробуйте ещё раз.")
        return ConversationHandler.END

    if animated:
        await query.message.reply_document(document=sticker, filename="emoji.tgs")
    else:
        await query.message.reply_sticker(sticker=sticker)
    await query.message.reply_text(f"✅ Премиум-эмодзи «{text}» готов!")

    # Кнопка добавления в набор
    keyboard = [[InlineKeyboardButton("➕ Добавить в мой набор", callback_data="add_to_set")]]
    await query.message.reply_text("Хотите добавить этот стикер в ваш личный набор?", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data["last_sticker"] = {"type": "emoji", "bytes": sticker, "animated": animated, "emoji": text[0]}
    return ConversationHandler.END

# ----------------------------------------------------------------------
# Диалог создания стикера из фото
# ----------------------------------------------------------------------
async def sticker_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("📸 Отправьте фотографию для премиум-стикера.")
    return PHOTO

async def photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте именно фотографию.")
        return PHOTO

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = bytes(photo_bytes)
    keyboard = [
        [InlineKeyboardButton("🖼 Статичный", callback_data="photo_static"),
         InlineKeyboardButton("✨ Анимированный", callback_data="photo_animated")]
    ]
    await update.message.reply_text("Выберите тип стикера:", reply_markup=InlineKeyboardMarkup(keyboard))
    return FORMAT

async def photo_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    animated = query.data == "photo_animated"
    photo_bytes = context.user_data.get("photo_bytes")
    if not photo_bytes:
        await query.message.reply_text("⚠️ Фото не найдено. Попробуйте заново.")
        return ConversationHandler.END

    try:
        sticker = generate_photo_sticker(photo_bytes, animated)
    except Exception as e:
        logger.error(f"Generate photo sticker error: {e}")
        await query.message.reply_text("⚠️ Ошибка обработки фото.")
        return ConversationHandler.END

    if animated:
        await query.message.reply_document(document=sticker, filename="sticker.tgs")
    else:
        await query.message.reply_sticker(sticker=sticker)
    await query.message.reply_text("✅ Ваш премиум-стикер готов!")

    keyboard = [[InlineKeyboardButton("➕ Добавить в мой набор", callback_data="add_to_set")]]
    await query.message.reply_text("Хотите добавить этот стикер в ваш личный набор?", reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data["last_sticker"] = {"type": "photo", "bytes": sticker, "animated": animated, "emoji": "⭐"}
    return ConversationHandler.END

# ----------------------------------------------------------------------
# Добавление в стикерпак (общий обработчик)
# ----------------------------------------------------------------------
async def add_to_set_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    set_name = context.user_data.get("sticker_set")
    last = context.user_data.get("last_sticker")
    if not last:
        await query.message.reply_text("Нет стикера для добавления.")
        return

    if not set_name:
        # Предложить создать новый набор
        await query.message.reply_text("У вас ещё нет набора. Давайте создадим! Введите название набора (title):")
        context.user_data["awaiting_set_title"] = True
        return SET_TITLE
    else:
        success = await add_sticker_to_set(
            context, user_id, set_name, last["bytes"], last["emoji"], last["animated"]
        )
        if success:
            await query.message.reply_text(f"✅ Стикер добавлен в набор «{set_name}».")
        else:
            await query.message.reply_text("❌ Не удалось добавить стикер. Возможно, набор переполнен (лимит 120).")
        return ConversationHandler.END

async def set_title_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получаем название набора от пользователя."""
    if not context.user_data.get("awaiting_set_title"):
        return ConversationHandler.END
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Название не может быть пустым. Попробуйте ещё раз.")
        return SET_TITLE
    context.user_data["set_title"] = title
    context.user_data["awaiting_set_title"] = False
    # Проверим, есть ли стикер для добавления
    last = context.user_data.get("last_sticker")
    if not last:
        await update.message.reply_text("Нет стикера для первого добавления. Создайте стикер заново.")
        return ConversationHandler.END
    # Создаём набор
    set_name = await create_sticker_set_for_user(
        update, context, title, last["bytes"], last["emoji"], last["animated"]
    )
    if set_name:
        await update.message.reply_text(f"✅ Набор «{title}» создан! Стикер добавлен.")
        context.user_data["sticker_set"] = set_name
    return ConversationHandler.END

async def my_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает информацию о наборе пользователя."""
    query = update.callback_query
    await query.answer()
    set_name = context.user_data.get("sticker_set")
    if set_name:
        await query.message.reply_text(f"📦 Ваш набор стикеров: {set_name}")
    else:
        await query.message.reply_text(
            "У вас пока нет набора. Создайте стикер и добавьте его, или используйте /createset."
        )
    return ConversationHandler.END

# ----------------------------------------------------------------------
# Inline-режим
# ----------------------------------------------------------------------
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if len(query) > 5:
        query = query[:5]
    if not query:
        return

    # Генерируем статичный и анимированный варианты, кэшируем file_id
    results = []
    # Статичный
    static_key = f"static_{query}"
    static_id = context.bot_data.get(static_key)
    if not static_id and CACHE_CHAT_ID:
        try:
            sticker_bytes = generate_emoji_sticker(query, animated=False)
            msg = await context.bot.send_sticker(chat_id=CACHE_CHAT_ID, sticker=sticker_bytes)
            static_id = msg.sticker.file_id
            context.bot_data[static_key] = static_id
            await context.bot.delete_message(chat_id=CACHE_CHAT_ID, message_id=msg.message_id)
        except Exception as e:
            logger.error(f"Inline static cache error: {e}")
    if static_id:
        results.append(
            InlineQueryResultCachedSticker(
                id=f"static_{query}",
                sticker_file_id=static_id,
            )
        )

    # Анимированный
    anim_key = f"anim_{query}"
    anim_id = context.bot_data.get(anim_key)
    if not anim_id and CACHE_CHAT_ID:
        try:
            anim_bytes = generate_emoji_sticker(query, animated=True)
            msg = await context.bot.send_document(chat_id=CACHE_CHAT_ID, document=anim_bytes, filename="emoji.tgs")
            # Для TGS file_id берём из документа, но sticker должен быть отправлен как стикер?
            # Документ не даст file_id стикера. Нужно отправлять как стикер.
            # API позволяет отправить tgs как sticker: send_sticker с .tgs.
            # Переделаем: отправим как стикер (бота принимает .tgs как анимированный стикер)
            # Попробуем через send_sticker.
            await context.bot.delete_message(chat_id=CACHE_CHAT_ID, message_id=msg.message_id)
            # Отправим как стикер
            msg2 = await context.bot.send_sticker(chat_id=CACHE_CHAT_ID, sticker=anim_bytes)
            anim_id = msg2.sticker.file_id
            context.bot_data[anim_key] = anim_id
            await context.bot.delete_message(chat_id=CACHE_CHAT_ID, message_id=msg2.message_id)
        except Exception as e:
            logger.error(f"Inline animated cache error: {e}")
    if anim_id:
        results.append(
            InlineQueryResultCachedSticker(
                id=f"anim_{query}",
                sticker_file_id=anim_id,
            )
        )

    if results:
        await update.inline_query.answer(results, cache_time=10)
    else:
        await update.inline_query.answer([], cache_time=10)

# ----------------------------------------------------------------------
# Главная функция
# ----------------------------------------------------------------------
def main():
    logger.info("Запуск бота «Премиум Стикеры и Эмодзи»...")
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        logger.error("Не задан BOT_TOKEN. Укажите в переменной окружения или в коде.")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler для создания эмодзи
    emoji_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(emoji_start, pattern="^create_emoji$")],
        states={
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_receive_text)],
            FORMAT: [CallbackQueryHandler(emoji_format_choice, pattern="^format_")],
        },
        fallbacks=[],
    )

    # ConversationHandler для создания стикера из фото
    sticker_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(sticker_start, pattern="^create_sticker$")],
        states={
            PHOTO: [
                MessageHandler(filters.PHOTO, photo_receive),
                MessageHandler(~filters.COMMAND, lambda u, c: u.message.reply_text("Жду фотографию...")),
            ],
            FORMAT: [CallbackQueryHandler(photo_format_choice, pattern="^photo_")],
        },
        fallbacks=[],
    )

    # Обработчик добавления в набор и создания набора
    add_set_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_to_set_handler, pattern="^add_to_set$")],
        states={
            SET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_title_receive)],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(help_command, pattern="^help$"))
    application.add_handler(CallbackQueryHandler(my_set_command, pattern="^my_set$"))
    application.add_handler(emoji_conv)
    application.add_handler(sticker_conv)
    application.add_handler(add_set_conv)

    if CACHE_CHAT_ID:
        application.add_handler(InlineQueryHandler(inline_query))
        logger.info(f"Inline-режим включён, кэш-чат ID: {CACHE_CHAT_ID}")
    else:
        logger.warning("Inline-режим отключён (CACHE_CHAT_ID=0).")

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
