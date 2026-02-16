#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - ПОЛНАЯ РАБОЧАЯ ВЕРСИЯ С КРАСИВЫМ ФОРМАТИРОВАНИЕМ

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
    """Форматирование текста погоды (запасной вариант)"""
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
    
    # Формируем красивый ответ с эмодзи и переносами строк
    text = (
        f"📍 *{payload['location_short']}*\n\n"
        f"🌡️ *Сейчас:* {payload['temp_now']}°C {payload['weather_desc']}{feels_text}\n\n"
        f"📊 *Днем:* от {payload['temp_min']}°C до {payload['temp_max']}°C\n\n"
        f"💧 *Осадки:* {payload['precip']} мм\n\n"
        f"💡 *Совет:* {advice}"
    )
    return text

async def get_groq_weather(payload: dict) -> str | None:
    """Получение красивого описания от Groq"""
    system = (
        "Ты дружелюбный помощник, который дает прогноз погоды. "
        "Твои ответы должны быть:\n"
        "- Информативными (температура, осадки, ощущения)\n"
        "- Разбитыми на абзацы (используй \\n\\n)\n"
        "- С дружелюбным тоном\n"
        "- Без markdown, только текст\n\n"
        "ПРИМЕР:\n"
        "В Москве сейчас −5°C и снежно. Ощущается как −10°C.\n\n"
        "В течение дня: от −8°C до −2°C. Ожидается небольшой снег.\n\n"
        "Одевайтесь теплее и будьте осторожны на дорогах!"
    )

    user = (
        f"Данные о погоде для {payload['location_short']}:\n"
        f"Сейчас: {payload['temp_now']}°C, ощущается как {payload['feels_like']}°C\n"
        f"Состояние: {payload['weather_desc']}\n"
        f"Сегодня: от {payload['temp_min']}°C до {payload['temp_max']}°C\n"
        f"Осадки: {payload['precip']} мм\n"
        f"Сформируй короткий прогноз из 3-4 предложений, разделенных пустыми строками."
    )

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
        f"👋 *Привет, {user.first_name}!*\n\n"
        f"Я помогу узнать погоду в любом городе.\n"
        f"Просто напиши название города или используй кнопки ниже.",
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
        logger.info(f"🔴 НАЖАТА КНОПКА: Старт")
        if user_id in user_cities:
            del user_cities[user_id]
        await update.message.reply_text(
            "Введите название города:",
            reply_markup=keyboard
        )
        return
    
    elif text == BTN_UPDATE:
        logger.info(f"🟢 НАЖАТА КНОПКА: Обновить прогноз")
        if user_id not in user_cities:
            await update.message.reply_text(
                "Сначала введите название города!",
                reply_markup=keyboard
            )
            return
        
        city = user_cities[user_id]
        logger.info(f"🔄 Обновляем прогноз для города: {city}")
        await update.message.reply_text(
            f"🔄 Обновляю прогноз для *{city}*...",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await send_weather(update, city)
        return
    
    # ===== ОБРАБОТКА ВВОДА ГОРОДА =====
    logger.info(f"🏙️ Ввод города: {text}")
    
    # Сохраняем город
    user_cities[user_id] = text
    
    await update.message.reply_text(
        f"🔍 Ищу погоду для *{text}*...",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await send_weather(update, text)

async def send_weather(update: Update, city: str):
    """Отправка прогноза"""
    user = update.effective_user
    
    try:
        # Получаем координаты
        geo = geocode_city(city)
        if not geo:
            await update.message.reply_text(
                f"❌ Город *'{city}'* не найден.\n\nПопробуйте написать по-другому (например, 'Москва' вместо 'Moscow').",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            return
        
        # Получаем погоду
        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        
        # Формируем данные
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        
        # Пробуем получить ответ от Groq
        groq_text = await get_groq_weather(payload)
        
        if groq_text and len(groq_text.split()) > 10:  # Если ответ содержательный
            final_text = groq_text
        else:
            # Используем запасной вариант
            final_text = format_weather_text(payload)
        
        logger.info(f"✅ Отправляем прогноз для @{user.username}")
        await update.message.reply_text(
            final_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка сети: {e}")
        await update.message.reply_text(
            "❌ Ошибка при получении данных погоды.\n"
            "Сервер погоды временно недоступен. Попробуйте позже.",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Произошла внутренняя ошибка.\n"
            "Попробуйте еще раз через несколько минут.",
            reply_markup=keyboard
        )

# ================== ЗАПУСК ==================
async def main():
    """Главная функция"""
    logger.info("🚀 Запуск бота...")
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Обработчики зарегистрированы")
    
    # Запускаем бота
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    logger.info("✅ Бот успешно запущен и готов к работе!")
    
    # Держим бота запущенным
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await app.stop()

# ================== ТОЧКА ВХОДА ==================
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise
