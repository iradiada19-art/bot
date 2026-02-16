#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - РАБОЧАЯ ВЕРСИЯ

import os
import asyncio
import logging
import requests
from groq import Groq
from datetime import datetime, time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
BTN_REMINDERS = "Мои напоминания"  # Добавил кнопку напоминаний

# Создаем клавиатуру
keyboard = ReplyKeyboardMarkup(
    [[BTN_START, BTN_UPDATE], [BTN_REMINDERS]],
    resize_keyboard=True,
)

# Инициализация Groq клиента
groq_client = Groq(api_key=GROQ_API_KEY)

# Словарь для хранения городов пользователей
user_cities = {}

# Словарь кодов погоды на русском
WEATHER_CODE_RU = {
    0: "☀️ ясно",
    1: "🌤 в основном ясно",
    2: "⛅ переменная облачность",
    3: "☁️ пасмурно",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌧 морось",
    53: "🌧 морось",
    55: "🌧 сильная морось",
    61: "🌧 небольшой дождь",
    63: "🌧 дождь",
    65: "🌧 сильный дождь",
    71: "🌨 небольшой снег",
    73: "🌨 снег",
    75: "🌨 сильный снег",
    77: "🌨 снежная крупа",
    80: "🌧 ливень",
    81: "🌧 ливень",
    82: "🌧 сильный ливень",
    85: "🌨 снегопад",
    86: "🌨 сильный снегопад",
    95: "⛈ гроза",
    96: "⛈ гроза с градом",
    99: "⛈ сильная гроза",
}

# ================== ФУНКЦИИ ПОГОДЫ ==================
def geocode_city(city: str) -> dict | None:
    """Получение координат города"""
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
    """Получение погоды"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,apparent_temperature",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 1,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def build_weather_payload(city_label: str, geo: dict, wx: dict) -> dict:
    """Формирование данных о погоде"""
    current = wx.get("current", {}) or {}
    daily = wx.get("daily", {}) or {}
    
    # Определяем регион и страну
    region_parts = []
    if geo.get('admin1'):
        region_parts.append(geo['admin1'])
    if geo.get('country'):
        region_parts.append(geo['country'])
    
    location_full = city_label
    if region_parts:
        location_full = f"{city_label}, {', '.join(region_parts)}"
    
    # Получаем описание погоды с эмодзи
    weather_code = current.get("weather_code")
    weather_desc = WEATHER_CODE_RU.get(weather_code, "🌈 неизвестно")
    
    return {
        "location": location_full,
        "location_short": city_label,
        "temp_now": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "temp_min": (daily.get("temperature_2m_min") or [None])[0],
        "temp_max": (daily.get("temperature_2m_max") or [None])[0],
        "precip": (daily.get("precipitation_sum") or [0])[0],
        "weather_desc": weather_desc,
        "weather_code": weather_code,
    }

def format_weather_text(payload: dict) -> str:
    """Форматирование текста погоды"""
    feels = payload['feels_like']
    feels_text = f" (ощущается как {feels}°C)" if feels else ""
    
    # Определяем рекомендации по одежде
    temp = payload['temp_now']
    if temp < -20:
        advice = "🥶 Очень холодно! Одевайтесь максимально тепло."
    elif temp < -10:
        advice = "🧥 Холодно. Не забудьте шапку и перчатки."
    elif temp < 0:
        advice = "🧥 Прохладно. Лучше надеть куртку."
    elif temp < 10:
        advice = "🧥 Свежо. Легкая куртка не помешает."
    elif temp < 20:
        advice = "👕 Комфортная температура. Можно гулять!"
    else:
        advice = "👕 Тепло. Легкая одежда подойдет."
    
    text = (
        f"📍 *{payload['location_short']}*\n\n"
        f"🌡️ *Сейчас:* {payload['temp_now']}°C {payload['weather_desc']}{feels_text}\n\n"
        f"📊 *Днем:* от {payload['temp_min']}°C до {payload['temp_max']}°C\n\n"
        f"💧 *Осадки:* {payload['precip']} мм\n\n"
        f"💡 *Совет:* {advice}"
    )
    return text

def format_morning_text(payload: dict) -> str:
    """Утреннее приветствие с прогнозом"""
    import random
    morning_phrases = [
        "☀️ Доброе утро!",
        "🌅 С добрым утром!",
        "☀️ Просыпайтесь!",
        "🌤 Хорошего утра!",
        "☀️ Новый день начинается!",
    ]
    greeting = random.choice(morning_phrases)
    
    temp_avg = (payload['temp_min'] + payload['temp_max']) // 2
    
    text = (
        f"{greeting}\n\n"
        f"📅 *Прогноз на сегодня:*\n"
        f"{payload['weather_desc']}\n"
        f"🌡️ Средняя температура: {temp_avg}°C\n"
        f"💧 Осадки: {payload['precip']} мм\n\n"
        f"💪 Хорошего продуктивного дня!"
    )
    return text

def format_evening_text(payload: dict) -> str:
    """Вечернее пожелание"""
    import random
    evening_phrases = [
        "🌙 Спокойной ночи!",
        "✨ Доброй ночи!",
        "🌙 Сладких снов!",
        "⭐ Хорошего отдыха!",
        "🌙 Приятных сновидений!",
    ]
    greeting = random.choice(evening_phrases)
    
    sweet_words = [
        "Пусть завтрашний день будет лучше сегодняшнего! 🌟",
        "Отдыхайте, вы сегодня отлично поработали! 💫",
        "Сны пусть будут только радужными! 🌈",
        "Завтра ждет новый день и новые победы! ⭐",
        "Вы сегодня были великолепны! 💖",
    ]
    sweet = random.choice(sweet_words)
    
    tomorrow_temp = (payload['temp_min'] + payload['temp_max']) // 2
    
    text = (
        f"{greeting}\n\n"
        f"📊 *Сегодня было:*\n"
        f"🌡️ {payload['temp_now']}°C, {payload['weather_desc']}\n\n"
        f"💫 *На завтра:* примерно {tomorrow_temp}°C\n\n"
        f"{sweet}"
    )
    return text

async def get_groq_weather(payload: dict, text_type: str = "normal") -> str | None:
    """Получение красивого описания от Groq"""
    if text_type == "morning":
        system = "Ты доброе утро. Напиши короткое утреннее приветствие с прогнозом погоды на сегодня."
        user = f"Данные о погоде для {payload['location_short']}: Сейчас {payload['temp_now']}°C, {payload['weather_desc']}. Сегодня от {payload['temp_min']}°C до {payload['temp_max']}°C, осадки {payload['precip']} мм."
    elif text_type == "evening":
        system = "Ты нежный и заботливый. Напиши короткое вечернее пожелание спокойной ночи."
        user = f"Данные о погоде для {payload['location_short']}: Сейчас {payload['temp_now']}°C, {payload['weather_desc']}. Завтра от {payload['temp_min']}°C до {payload['temp_max']}°C."
    else:
        system = "Ты дружелюбный помощник. Дай прогноз погоды."
        user = f"В {payload['location_short']} сейчас {payload['temp_now']}°C, {payload['weather_desc']}, ощущается как {payload['feels_like']}°C. Сегодня от {payload['temp_min']}°C до {payload['temp_max']}°C, осадки {payload['precip']} мм."

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=250,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}")
        return None

# ================== ФУНКЦИИ РАССЫЛКИ ==================
async def send_morning_forecast(bot):
    """Утренняя рассылка в 8:00"""
    logger.info("⏰ Утренняя рассылка")
    if not user_cities:
        return
    
    for user_id, city in user_cities.items():
        try:
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            text = await get_groq_weather(payload, "morning")
            if not text:
                text = format_morning_text(payload)
            
            await bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Ошибка: {e}")

async def send_evening_message(bot):
    """Вечерняя рассылка в 22:00"""
    logger.info("🌙 Вечерняя рассылка")
    if not user_cities:
        return
    
    for user_id, city in user_cities.items():
        try:
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            text = await get_groq_weather(payload, "evening")
            if not text:
                text = format_evening_text(payload)
            
            await bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Ошибка: {e}")

# ================== ОБРАБОТЧИКИ КОМАНД ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка /start"""
    user = update.effective_user
    logger.info(f"👉 /start от @{user.username}")
    
    await update.message.reply_text(
        f"👋 *Привет, {user.first_name}!*\n\n"
        f"Я бот погоды. Напиши название города или используй кнопки.",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    text = update.message.text.strip()
    user = update.effective_user
    user_id = user.id
    
    logger.info(f"📨 Сообщение от @{user.username}: '{text}'")
    
    # ===== ОБРАБОТКА КНОПОК =====
    if text == BTN_START:
        logger.info("🔴 Кнопка Старт")
        await update.message.reply_text("Введите название города:", reply_markup=keyboard)
        return
    
    elif text == BTN_UPDATE:
        logger.info("🟢 Кнопка Обновить")
        if user_id not in user_cities:
            await update.message.reply_text("Сначала введите город!", reply_markup=keyboard)
            return
        
        city = user_cities[user_id]
        await update.message.reply_text(f"Обновляю прогноз для {city}...", reply_markup=keyboard)
        await send_weather(update, city)
        return
    
    elif text == BTN_REMINDERS:
        logger.info("🔵 Кнопка Напоминания")
        await update.message.reply_text(
            "📌 *Напоминания*\n\n"
            "Эта функция пока в разработке. Скоро тут можно будет создавать напоминания!",
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        return
    
    # ===== ОБРАБОТКА ВВОДА ГОРОДА =====
    logger.info(f"🏙️ Ввод города: {text}")
    user_cities[user_id] = text
    await update.message.reply_text(f"Ищу погоду для {text}...", reply_markup=keyboard)
    await send_weather(update, text)

async def send_weather(update: Update, city: str):
    """Отправка прогноза"""
    try:
        geo = geocode_city(city)
        if not geo:
            await update.message.reply_text(
                f"❌ Город '{city}' не найден.",
                reply_markup=keyboard
            )
            return
        
        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        
        groq_text = await get_groq_weather(payload, "normal")
        
        if groq_text:
            final_text = groq_text
        else:
            final_text = format_weather_text(payload)
        
        await update.message.reply_text(final_text, reply_markup=keyboard, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка. Попробуйте позже.", reply_markup=keyboard)

# ================== ЗАПУСК ==================
async def main():
    """Главная функция"""
    logger.info("🚀 Запуск бота...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Обработчики зарегистрированы")
    
    # Запускаем бота
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logger.info("✅ Бот запущен")
    
    # Планировщик для рассылок
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_morning_forecast, CronTrigger(hour=8, minute=0), args=[app.bot])
    scheduler.add_job(send_evening_message, CronTrigger(hour=22, minute=0), args=[app.bot])
    scheduler.start()
    logger.info("⏰ Планировщик запущен")
    
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown()
        await app.stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise
