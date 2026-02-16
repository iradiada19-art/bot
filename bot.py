#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - ИСПРАВЛЕННАЯ ВЕРСИЯ (с правильной обработкой кнопок)

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
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКА ЛОГИРОВАНИЯ ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    raise RuntimeError("TELEGRAM_BOT_TOKEN обязательно должен быть задан")

if not GROQ_API_KEY:
    logger.error("❌ GROQ_API_KEY не найден!")
    raise RuntimeError("GROQ_API_KEY обязательно должен быть задан")

logger.info("✅ Токены успешно загружены")

# ================== КОНСТАНТЫ ==================
ASK_CITY = 1

BTN_START = "Старт"
BTN_UPDATE = "Обновить прогноз"

# Создаем клавиатуру
keyboard = ReplyKeyboardMarkup(
    [[BTN_START, BTN_UPDATE]],
    resize_keyboard=True,
    one_time_keyboard=False,
)

# Инициализация Groq клиента
groq_client = Groq(api_key=GROQ_API_KEY)

WEATHER_CODE_RU = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь/туман",
    51: "морось слабая",
    53: "морось умеренная",
    55: "морось сильная",
    56: "ледяная морось слабая",
    57: "ледяная морось сильная",
    61: "дождь слабый",
    63: "дождь умеренный",
    65: "дождь сильный",
    66: "ледяной дождь слабый",
    67: "ледяной дождь сильный",
    71: "снег слабый",
    73: "снег умеренный",
    75: "снег сильный",
    77: "снежные зерна",
    80: "ливень слабый",
    81: "ливень умеренный",
    82: "ливень сильный",
    85: "снегопад слабый",
    86: "снегопад сильный",
    95: "гроза",
    96: "гроза с градом (слабым)",
    99: "гроза с градом (сильным)",
}

# ================== ФУНКЦИИ РАБОТЫ С ПОГОДОЙ ==================
def geocode_city(city: str) -> dict | None:
    """Получение координат города по названию"""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "ru", "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        return results[0]
    except Exception as e:
        logger.error(f"Ошибка геокодинга для города {city}: {e}")
        return None

def fetch_today_weather(lat: float, lon: float) -> dict:
    """Получение погоды по координатам"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 1,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Ошибка получения погоды для координат {lat},{lon}: {e}")
        raise

def build_weather_payload(city_label: str, geo: dict, wx: dict) -> dict:
    """Формирование структуры данных о погоде"""
    current = wx.get("current", {}) or {}
    daily = (wx.get("daily", {}) or {})

    temp_now = current.get("temperature_2m")
    wcode = current.get("weather_code")

    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    precip = (daily.get("precipitation_sum") or [None])[0]

    tz = wx.get("timezone")
    time_now = current.get("time")

    region_bits = []
    admin1 = geo.get("admin1")
    country = geo.get("country")
    if admin1:
        region_bits.append(str(admin1))
    if country:
        region_bits.append(str(country))

    location = city_label
    if region_bits:
        location = f"{city_label}, " + ", ".join(region_bits)

    return {
        "location": location,
        "timezone": tz,
        "time_now": time_now,
        "temp_now_c": temp_now,
        "temp_min_c": tmin,
        "temp_max_c": tmax,
        "precip_sum_mm": precip,
        "weather_code": wcode,
        "weather_desc_ru": WEATHER_CODE_RU.get(wcode, "неизвестно"),
    }

def groq_format_weather(payload: dict) -> str:
    """Генерация текстового описания погоды через Groq AI"""
    system = (
        "Ты русскоязычный ИИ ассистент. "
        "Сформируй короткий, понятный ответ о погоде на сегодня. "
        "Без форматирования, без списков, без эмодзи, без markdown. "
        "Один абзац. Если каких-то данных нет, не выдумывай."
    )

    user = (
        "Данные о погоде на сегодня:\n"
        f"Локация: {payload.get('location')}\n"
        f"Локальное время источника: {payload.get('time_now')} (timezone: {payload.get('timezone')})\n"
        f"Сейчас температура: {payload.get('temp_now_c')} °C\n"
        f"Минимум сегодня: {payload.get('temp_min_c')} °C\n"
        f"Максимум сегодня: {payload.get('temp_max_c')} °C\n"
        f"Осадки за день: {payload.get('precip_sum_mm')} мм\n"
        f"Состояние: {payload.get('weather_desc_ru')} (код {payload.get('weather_code')})\n"
        "Сформируй ответ пользователю."
    )

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=220,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}")
        return "Не удалось сформировать описание погоды."

# ================== ОБРАБОТЧИКИ КОМАНД ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка команды /start"""
    user = update.effective_user
    logger.info(f"Пользователь @{user.username} ({user.first_name}) запустил бота")
    
    # Очищаем сохраненный город
    context.user_data.pop("city", None)
    
    # Отправляем приветствие и просим ввести город
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\nВведите название вашего города, и я покажу прогноз погоды.",
        reply_markup=keyboard
    )
    return ASK_CITY

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка нажатий на кнопки"""
    text = update.message.text.strip()
    user = update.effective_user
    logger.info(f"Пользователь @{user.username} нажал кнопку: '{text}'")

    if text == BTN_START:
        # Кнопка "Старт" - начинаем заново
        context.user_data.pop("city", None)
        await update.message.reply_text(
            "Введите название вашего города.",
            reply_markup=keyboard
        )
        return ASK_CITY

    elif text == BTN_UPDATE:
        # Кнопка "Обновить прогноз"
        city = context.user_data.get("city")
        
        if not city:
            await update.message.reply_text(
                "Сначала введите название города.",
                reply_markup=keyboard
            )
            return ASK_CITY
        
        await update.message.reply_text(f"Обновляю прогноз для города {city}...", reply_markup=keyboard)
        await send_weather(update, context, city)
        return ConversationHandler.END
    
    # Если это не кнопка, а что-то другое
    return await handle_message(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка текстовых сообщений (ввод города)"""
    text = update.message.text.strip()
    user = update.effective_user
    
    logger.info(f"Пользователь @{user.username} отправил сообщение: '{text}'")
    
    # Проверяем, не является ли сообщение командой или кнопкой
    if text in [BTN_START, BTN_UPDATE]:
        return await handle_buttons(update, context)
    
    if text.startswith('/'):
        await update.message.reply_text(
            "Используйте кнопки или введите название города.",
            reply_markup=keyboard
        )
        return ASK_CITY
    
    # Сохраняем город
    context.user_data["city"] = text
    logger.info(f"Сохраняем город '{text}' для пользователя @{user.username}")
    
    await update.message.reply_text(f"Ищу погоду для города {text}...", reply_markup=keyboard)
    await send_weather(update, context, text)
    return ConversationHandler.END

async def city_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Функция для обработки ввода города в состоянии ASK_CITY"""
    return await handle_message(update, context)

async def send_weather(update: Update, context: ContextTypes.DEFAULT_TYPE, city: str) -> None:
    """Отправка прогноза погоды пользователю"""
    user = update.effective_user
    
    try:
        logger.info(f"Получаем координаты для города: {city}")
        geo = geocode_city(city)
        
        if not geo:
            logger.warning(f"Город '{city}' не найден для пользователя @{user.username}")
            await update.message.reply_text(
                f"Город '{city}' не найден. Попробуйте написать по-другому (например, 'Москва' вместо 'Moscow').", 
                reply_markup=keyboard
            )
            return

        lat = geo["latitude"]
        lon = geo["longitude"]
        city_full = geo.get("name", city)
        country = geo.get("country", "")
        
        logger.info(f"Найден город: {city_full}, {country} (координаты: {lat}, {lon})")
        
        wx = fetch_today_weather(lat, lon)
        payload = build_weather_payload(city_full, geo, wx)
        
        logger.info(f"Формируем описание погоды через Groq для города {city_full}")
        text = groq_format_weather(payload)

        logger.info(f"Отправлен прогноз для {city_full} пользователю @{user.username}")
        await update.message.reply_text(text, reply_markup=keyboard)

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении погоды для {city}: {e}")
        await update.message.reply_text(
            "Ошибка при получении данных погоды. Сервер погоды временно недоступен.", 
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке погоды: {e}", exc_info=True)
        await update.message.reply_text(
            "Произошла внутренняя ошибка. Попробуйте позже.", 
            reply_markup=keyboard
        )

# ================== ОСНОВНАЯ ФУНКЦИЯ ==================
async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск бота...")
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Создаем обработчик диалога
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_cmd)],
        states={
            ASK_CITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, city_input),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^({BTN_START}|{BTN_UPDATE})$"), handle_buttons),
        ],
        name="weather_conversation",
        persistent=False,
    )

    # Добавляем обработчики
    app.add_handler(conv_handler)
    
    # Добавляем обработчик для кнопок вне диалога
    app.add_handler(MessageHandler(
        filters.Regex(f"^({BTN_START}|{BTN_UPDATE})$"), 
        handle_buttons
    ))

    logger.info("✅ Обработчики зарегистрированы")

    # Запускаем бота
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.info("✅ Бот успешно запущен и готов к работе!")
    
    # Держим бота запущенным
    while True:
        await asyncio.sleep(3600)

# ================== ТОЧКА ВХОДА ==================
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise
