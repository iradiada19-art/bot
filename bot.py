#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - С РАССЫЛКОЙ ПО РАСПИСАНИЮ

import os
import asyncio
import logging
import requests
from groq import Groq
from datetime import datetime, time
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from telegram import Update, ReplyKeyboardMarkup, Bot
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
    # Мотивирующие фразы
    morning_phrases = [
        "☀️ Доброе утро!",
        "🌅 С добрым утром!",
        "☀️ Просыпайтесь!",
        "🌤 Хорошего утра!",
        "☀️ Новый день начинается!",
    ]
    import random
    greeting = random.choice(morning_phrases)
    
    # Короткий прогноз
    temp_avg = (payload['temp_min'] + payload['temp_max']) // 2
    
    weather_icon = "☀️" if payload['temp_now'] > 0 else "❄️"
    
    text = (
        f"{greeting}\n\n"
        f"📅 *Прогноз на сегодня:*\n"
        f"{weather_icon} {payload['weather_desc']}\n"
        f"🌡️ Средняя температура: {temp_avg}°C\n"
        f"💧 Осадки: {payload['precip']} мм\n\n"
        f"💪 Хорошего продуктивного дня!"
    )
    return text

def format_evening_text(payload: dict) -> str:
    """Вечернее пожелание"""
    # Ласковые слова
    evening_phrases = [
        "🌙 Спокойной ночи!",
        "✨ Доброй ночи!",
        "🌙 Сладких снов!",
        "⭐ Хорошего отдыха!",
        "🌙 Приятных сновидений!",
    ]
    import random
    greeting = random.choice(evening_phrases)
    
    # Милые дополнения
    sweet_words = [
        "Пусть завтрашний день будет лучше сегодняшнего! 🌟",
        "Отдыхайте, вы сегодня отлично поработали! 💫",
        "Сны пусть будут только радужными! 🌈",
        "Завтра ждет новый день и новые победы! ⭐",
        "Вы сегодня были великолепны! 💖",
    ]
    sweet = random.choice(sweet_words)
    
    # Короткий прогноз на завтра (для вечера)
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
        system = (
            "Ты доброе утро. Напиши короткое утреннее приветствие с прогнозом погоды на сегодня. "
            "Используй данные о погоде: температуру, осадки. Добавь мотивирующую фразу. "
            "Ответ должен быть 3-4 предложения, разделенных пустыми строками."
        )
        user = (
            f"Данные о погоде для {payload['location_short']}:\n"
            f"Сейчас: {payload['temp_now']}°C\n"
            f"Состояние: {payload['weather_desc']}\n"
            f"Сегодня: от {payload['temp_min']}°C до {payload['temp_max']}°C\n"
            f"Осадки: {payload['precip']} мм\n"
            f"Напиши утреннее приветствие с прогнозом."
        )
    elif text_type == "evening":
        system = (
            "Ты нежный и заботливый. Напиши короткое вечернее пожелание спокойной ночи. "
            "Упомяни погоду сегодня и коротко на завтра. Добавь ласковые слова. "
            "Ответ должен быть 3-4 предложения."
        )
        user = (
            f"Данные о погоде для {payload['location_short']}:\n"
            f"Сейчас: {payload['temp_now']}°C, {payload['weather_desc']}\n"
            f"Завтра ожидается: от {payload['temp_min']}°C до {payload['temp_max']}°C\n"
            f"Напиши вечернее пожелание."
        )
    else:
        system = (
            "Ты дружелюбный помощник. Дай прогноз погоды из 3-4 предложений, "
            "разделенных пустыми строками. Без markdown."
        )
        user = (
            f"Данные о погоде для {payload['location_short']}:\n"
            f"Сейчас: {payload['temp_now']}°C, ощущается как {payload['feels_like']}°C\n"
            f"Состояние: {payload['weather_desc']}\n"
            f"Сегодня: от {payload['temp_min']}°C до {payload['temp_max']}°C\n"
            f"Осадки: {payload['precip']} мм"
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

# ================== ФУНКЦИИ РАССЫЛКИ ==================
async def send_morning_forecast(bot: Bot):
    """Утренняя рассылка в 8:00"""
    logger.info("⏰ Запуск утренней рассылки")
    
    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return
    
    for user_id, city in user_cities.items():
        try:
            logger.info(f"Отправляем утренний прогноз для пользователя {user_id}, город {city}")
            
            # Получаем погоду
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            # Пробуем получить от Groq
            text = await get_groq_weather(payload, "morning")
            
            if not text:
                # Запасной вариант
                text = format_morning_text(payload)
            
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='Markdown'
            )
            logger.info(f"✅ Утренний прогноз отправлен пользователю {user_id}")
            await asyncio.sleep(0.5)  # Небольшая задержка между сообщениями
            
        except Exception as e:
            logger.error(f"Ошибка при отправке утреннего прогноза пользователю {user_id}: {e}")

async def send_evening_message(bot: Bot):
    """Вечерняя рассылка в 22:00"""
    logger.info("🌙 Запуск вечерней рассылки")
    
    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return
    
    for user_id, city in user_cities.items():
        try:
            logger.info(f"Отправляем вечернее пожелание пользователю {user_id}")
            
            # Получаем погоду
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            # Пробуем получить от Groq
            text = await get_groq_weather(payload, "evening")
            
            if not text:
                # Запасной вариант
                text = format_evening_text(payload)
            
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='Markdown'
            )
            logger.info(f"✅ Вечернее сообщение отправлено пользователю {user_id}")
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке вечернего сообщения пользователю {user_id}: {e}")

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
        f"Просто напиши название города или используй кнопки ниже.\n\n"
        f"⏰ *Бонус:* Я буду сам присылать прогноз в 8:00 и желать спокойной ночи в 22:00!",
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
        geo = geocode_city(city)
        if not geo:
            await update.message.reply_text(
                f"❌ Город *'{city}'* не найден.\n\nПопробуйте написать по-другому.",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            return
        
        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        
        groq_text = await get_groq_weather(payload, "normal")
        
        if groq_text and len(groq_text.split()) > 10:
            final_text = groq_text
        else:
            final_text = format_weather_text(payload)
        
        logger.info(f"✅ Отправляем прогноз для @{user.username}")
        await update.message.reply_text(
            final_text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка. Попробуйте позже.",
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
    
    logger.info("✅ Бот запущен")
    
    # ===== НАСТРАИВАЕМ РАСПИСАНИЕ =====
    scheduler = AsyncIOScheduler()
    
    # Утренняя рассылка в 8:00
    scheduler.add_job(
        send_morning_forecast,
        CronTrigger(hour=8, minute=0),
        args=[app.bot],
        id="morning_forecast"
    )
    logger.info("⏰ Запланирована утренняя рассылка на 8:00")
    
    # Вечерняя рассылка в 22:00
    scheduler.add_job(
        send_evening_message,
        CronTrigger(hour=22, minute=0),
        args=[app.bot],
        id="evening_message"
    )
    logger.info("⏰ Запланирована вечерняя рассылка на 22:00")
    
    scheduler.start()
    logger.info("⏰ Планировщик запущен")
    
    # Держим бота запущенным
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown()
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
