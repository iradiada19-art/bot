#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - МАКСИМАЛЬНО ПРОСТАЯ ВЕРСИЯ

import os
import asyncio
import logging
import requests
from groq import Groq

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ================== НАСТРОЙКА ЛОГИРОВАНИЯ ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = "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"

if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    raise RuntimeError("TELEGRAM_BOT_TOKEN обязательно должен быть задан")

if not GROQ_API_KEY:
    logger.error("❌ GROQ_API_KEY не найден!")
    raise RuntimeError("GROQ_API_KEY обязательно должен быть задан")

logger.info("✅ Токены успешно загружены")

# ================== КОНСТАНТЫ ==================
BTN_START = "Старт"
BTN_UPDATE = "Обновить прогноз"

# Создаем клавиатуру
keyboard = ReplyKeyboardMarkup(
    [[BTN_START, BTN_UPDATE]],
    resize_keyboard=True,
)

# Инициализация Groq клиента
groq_client = Groq(api_key=GROQ_API_KEY)

# Словарь для хранения городов пользователей (проще чем context.user_data)
user_cities = {}

WEATHER_CODE_RU = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь/туман",
    51: "морось слабая", 53: "морось умеренная", 55: "морось сильная",
    56: "ледяная морось слабая", 57: "ледяная морось сильная",
    61: "дождь слабый", 63: "дождь умеренный", 65: "дождь сильный",
    66: "ледяной дождь слабый", 67: "ледяной дождь сильный",
    71: "снег слабый", 73: "снег умеренный", 75: "снег сильный",
    77: "снежные зерна",
    80: "ливень слабый", 81: "ливень умеренный", 82: "ливень сильный",
    85: "снегопад слабый", 86: "снегопад сильный",
    95: "гроза", 96: "гроза с градом (слабым)", 99: "гроза с градом (сильным)",
}

# ================== ФУНКЦИИ ПОГОДЫ ==================
def geocode_city(city: str) -> dict | None:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "ru", "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        return results[0] if results else None
    except Exception as e:
        logger.error(f"Ошибка геокодинга: {e}")
        return None

def fetch_today_weather(lat: float, lon: float) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto", "forecast_days": 1,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def build_weather_payload(city_label: str, geo: dict, wx: dict) -> dict:
    current = wx.get("current", {}) or {}
    daily = wx.get("daily", {}) or {}
    
    return {
        "location": f"{city_label}, {geo.get('country', '')}",
        "temp_now_c": current.get("temperature_2m"),
        "temp_min_c": (daily.get("temperature_2m_min") or [None])[0],
        "temp_max_c": (daily.get("temperature_2m_max") or [None])[0],
        "weather_desc_ru": WEATHER_CODE_RU.get(current.get("weather_code"), "неизвестно"),
    }

def groq_format_weather(payload: dict) -> str:
    system = "Ты русскоязычный ассистент. Опиши погоду на сегодня одним предложением."
    user = f"В городе {payload['location']} сейчас {payload['temp_now_c']}°C, {payload['weather_desc_ru']}. Минимум {payload['temp_min_c']}°C, максимум {payload['temp_max_c']}°C."
    
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=100,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка Groq: {e}")
        return f"Сейчас в {payload['location']} {payload['temp_now_c']}°C, {payload['weather_desc_ru']}."

# ================== ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка /start"""
    user = update.effective_user
    user_id = user.id
    logger.info(f"👉 /start от @{user.username}")
    
    # Удаляем сохраненный город
    if user_id in user_cities:
        del user_cities[user_id]
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\nВведите название города:",
        reply_markup=keyboard
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    text = update.message.text.strip()
    user = update.effective_user
    user_id = user.id
    
    logger.info(f"📨 Сообщение от @{user.username}: '{text}'")
    
    # ===== ОБРАБОТКА КНОПОК =====
    if text == BTN_START:
        logger.info(f"🔴 НАЖАТА КНОПКА: Старт")
        # Очищаем город
        if user_id in user_cities:
            del user_cities[user_id]
        await update.message.reply_text("Введите название города:", reply_markup=keyboard)
        return
    
    elif text == BTN_UPDATE:
        logger.info(f"🟢 НАЖАТА КНОПКА: Обновить прогноз")
        # Проверяем, есть ли сохраненный город
        if user_id not in user_cities:
            await update.message.reply_text(
                "Сначала введите название города!",
                reply_markup=keyboard
            )
            return
        
        city = user_cities[user_id]
        logger.info(f"🔄 Обновляем прогноз для города: {city}")
        await update.message.reply_text(f"Обновляю прогноз для {city}...", reply_markup=keyboard)
        await send_weather(update, city)
        return
    
    # ===== ОБРАБОТКА ВВОДА ГОРОДА =====
    logger.info(f"🏙️ Похоже на название города: {text}")
    
    # Сохраняем город
    user_cities[user_id] = text
    logger.info(f"💾 Сохраняем город {text} для пользователя @{user.username}")
    
    await update.message.reply_text(f"Ищу погоду для города {text}...", reply_markup=keyboard)
    await send_weather(update, text)

async def send_weather(update: Update, city: str):
    """Отправка прогноза с правильным форматированием"""
    user = update.effective_user
    
    try:
        # Получаем координаты
        geo = geocode_city(city)
        if not geo:
            await update.message.reply_text(
                f"❌ Город '{city}' не найден. Попробуйте еще раз:",
                reply_markup=keyboard
            )
            return
        
        # Получаем погоду
        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        
        # Формируем данные
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        
        # Пробуем получить красивый ответ от Groq
        try:
            text = groq_format_weather(payload)
            logger.info(f"✅ Получен ответ от Groq: {len(text)} символов")
        except Exception as e:
            logger.error(f"❌ Ошибка Groq: {e}")
            # Запасной вариант с форматированием
            feels_like = payload.get('feels_like_c')
            feels_text = f" (ощущается как {feels_like}°C)" if feels_like else ""
            
            text = (f"🌍 *{payload['location_short']}*\n\n"
                    f"🌡️ *Сейчас:* {payload['temp_now_c']}°C, {payload['weather_desc_ru']}{feels_text}\n\n"
                    f"📊 *Днем:* от {payload['temp_min_c']}°C до {payload['temp_max_c']}°C\n\n"
                    f"💧 *Осадки:* {payload['precip_sum_mm']} мм")
        
        # Отправляем с Markdown форматированием
        logger.info(f"✅ Отправляем прогноз для @{user.username}")
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'  # Включаем Markdown для жирного текста
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=keyboard
        )
            return
        
        # Получаем погоду
        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        
        # Формируем и отправляем прогноз
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        text = groq_format_weather(payload)
        
        logger.info(f"✅ Отправляем прогноз для @{user.username}")
        await update.message.reply_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text(
            "Произошла ошибка. Попробуйте позже.",
            reply_markup=keyboard
        )

# ================== ЗАПУСК ==================
async def main():
    logger.info("🚀 Запуск бота...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Обработчики зарегистрированы")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logger.info("✅ Бот запущен и готов к работе!")
    
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
