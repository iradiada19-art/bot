#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - ПОЛНАЯ ВЕРСИЯ: убрана кнопка "Поболтать" + фикс удаления заметок
# + добавлен ветер в прогноз + добавлена последняя строка со статистикой min/max

import json
import os
import asyncio
import logging
import requests
import re
from datetime import datetime, timedelta
from groq import Groq
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
    raise RuntimeError("TELEGRAM_BOT_TOKEN обязательно должен быть задан")

logger.info("✅ Токены успешно загружены")

# ================== ЧАСОВОЙ ПОЯС ==================
MSK_TZ = timezone('Europe/Moscow')
logger.info(f"🕐 Часовой пояс: {MSK_TZ}")

# ================== ФАЙЛЫ ДЛЯ СОХРАНЕНИЯ ==================
REMINDERS_FILE = "/tmp/reminders.json"
NOTES_FILE = "/tmp/notes.json"
logger.info(f"📁 Файл напоминаний: {REMINDERS_FILE}")
logger.info(f"📁 Файл заметок: {NOTES_FILE}")

# ================== КОНСТАНТЫ ==================
BTN_START = "Узнать погоду"
BTN_UPDATE = "Обновить прогноз"
BTN_REMINDERS = "Напоминания"
BTN_NOTES = "Мои заметки"

# Главная клавиатура (без "Поболтать")
main_keyboard = ReplyKeyboardMarkup(
    [[BTN_START, BTN_UPDATE], [BTN_REMINDERS, BTN_NOTES]],
    resize_keyboard=True,
)

# Клавиатура меню напоминаний
reminders_keyboard = ReplyKeyboardMarkup(
    [["📝 Создать", "📋 Список"], ["❌ Удалить", "🔙 Назад"]],
    resize_keyboard=True
)

# Клавиатура меню заметок
notes_keyboard = ReplyKeyboardMarkup(
    [["📝 Новая заметка", "📋 Все заметки"], ["❌ Удалить заметку", "🔙 Назад"]],
    resize_keyboard=True
)

# Инициализация Groq клиента (используется для генерации текста погоды)
try:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("✅ Groq клиент инициализирован")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации Groq: {e}")
    groq_client = None

# Хранилища данных
user_cities = {}        # {user_id: city_name}
user_reminders = {}     # {user_id: [{"id": 1, "text": "...", "time": "...", "job_id": "..."}]}
user_notes = {}         # {user_id: [{"id": 1, "text": "...", "date": "..."}]}
reminder_counter = 0
notes_counter = 0

# Состояния пользователей
# main / reminders / notes / new_note / deleting_note
user_state = {}         # {user_id: "main", ...}

# Планировщик
scheduler = None

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

# ================== ФУНКЦИИ СОХРАНЕНИЯ ==================
def save_reminders():
    """Сохраняет напоминания в файл"""
    try:
        save_data = {str(uid): reminders for uid, reminders in user_reminders.items()}
        with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

        total = sum(len(v) for v in user_reminders.values())
        logger.info(f"💾 Напоминания сохранены. Всего: {total}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения напоминаний: {e}")
        return False


def load_reminders():
    """Загружает напоминания из файла"""
    global user_reminders, reminder_counter
    try:
        if os.path.exists(REMINDERS_FILE):
            with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            user_reminders = {int(k): v for k, v in save_data.items()}

            max_id = 0
            for reminders in user_reminders.values():
                for rem in reminders:
                    max_id = max(max_id, rem.get('id', 0))
            reminder_counter = max_id

            total = sum(len(v) for v in user_reminders.values())
            logger.info(f"✅ Загружено напоминаний: {total}")
        else:
            user_reminders = {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки напоминаний: {e}")
        user_reminders = {}


def save_notes():
    """Сохраняет заметки в файл"""
    try:
        save_data = {str(uid): notes for uid, notes in user_notes.items()}
        with open(NOTES_FILE, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)

        total = sum(len(v) for v in user_notes.values())
        logger.info(f"💾 Заметки сохранены. Всего: {total}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения заметок: {e}")
        return False


def load_notes():
    """Загружает заметки из файла"""
    global user_notes, notes_counter
    try:
        if os.path.exists(NOTES_FILE):
            with open(NOTES_FILE, 'r', encoding='utf-8') as f:
                save_data = json.load(f)

            user_notes = {int(k): v for k, v in save_data.items()}

            max_id = 0
            for notes in user_notes.values():
                for note in notes:
                    max_id = max(max_id, note.get('id', 0))
            notes_counter = max_id

            total = sum(len(v) for v in user_notes.values())
            logger.info(f"✅ Загружено заметок: {total}")
        else:
            user_notes = {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки заметок: {e}")
        user_notes = {}

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
    """Получение погоды (добавлен ветер wind_speed_10m)"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,apparent_temperature,wind_speed_10m",
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

    region_parts = []
    if geo.get('admin1'):
        region_parts.append(geo['admin1'])
    if geo.get('country'):
        region_parts.append(geo['country'])

    location_full = city_label
    if region_parts:
        location_full = f"{city_label}, {', '.join(region_parts)}"

    weather_code = current.get("weather_code")
    weather_desc = WEATHER_CODE_RU.get(weather_code, "🌈 неизвестно")

    return {
        "location": location_full,
        "location_short": city_label,
        "temp_now": current.get("temperature_2m"),
        "feels_like": current.get("apparent_temperature"),
        "wind_speed": current.get("wind_speed_10m"),  # <-- добавили ветер
        "temp_min": (daily.get("temperature_2m_min") or [None])[0],
        "temp_max": (daily.get("temperature_2m_max") or [None])[0],
        "precip": (daily.get("precipitation_sum") or [0])[0],
        "weather_desc": weather_desc,
        "weather_code": weather_code,
    }


def _append_minmax_stats(text: str, payload: dict) -> str:
    """Добавляет последней строкой статистику минимума/максимума температуры"""
    tmin = payload.get("temp_min")
    tmax = payload.get("temp_max")
    if tmin is None or tmax is None:
        return text.rstrip()
    # Последней строкой (без лишних строк после)
    return text.rstrip() + f"\n\n📈 *Статистика дня:* минимум {tmin}°C / максимум {tmax}°C"


def format_weather_text(payload: dict) -> str:
    """Локальное форматирование текста погоды (с ветром)"""
    feels = payload.get('feels_like')
    feels_text = f" (ощущается как {feels}°C)" if feels is not None else ""

    wind = payload.get("wind_speed")
    wind_text = f"🌬️ *Ветер:* {wind} м/с\n\n" if wind is not None else ""

    temp = payload.get('temp_now')
    if temp is None:
        advice = "💡 Совет: проверь город ещё раз — данные не пришли."
    elif temp < -20:
        advice = "🥶 Очень холодно! Одевайся максимально тепло."
    elif temp < -10:
        advice = "🧥 Холодно. Не забудь шапку и перчатки."
    elif temp < 0:
        advice = "🧥 Прохладно. Лучше надеть куртку."
    elif temp < 10:
        advice = "🧥 Свежо. Легкая куртка не помешает."
    elif temp < 20:
        advice = "👕 Комфортная температура. Можно гулять!"
    else:
        advice = "👕 Тепло. Легкая одежда подойдет."

    base = (
        f"📍 *{payload['location_short']}*\n\n"
        f"🌡️ *Сейчас:* {payload['temp_now']}°C {payload['weather_desc']}{feels_text}\n\n"
        f"{wind_text}"
        f"💧 *Осадки:* {payload['precip']} мм\n\n"
        f"💡 *Совет:* {advice}"
    )
    return _append_minmax_stats(base, payload)


def format_morning_text(payload: dict) -> str:
    """Утреннее приветствие"""
    import random
    phrases = ["☀️ Доброе утро!", "🌅 С добрым утром!", "☀️ Просыпайся!"]

    wind = payload.get("wind_speed")
    wind_line = f"🌬️ Ветер: {wind} м/с\n" if wind is not None else ""

    temp_avg = "—"
    if payload.get('temp_min') is not None and payload.get('temp_max') is not None:
        temp_avg = (payload['temp_min'] + payload['temp_max']) // 2

    base = (
        f"{random.choice(phrases)}\n\n"
        f"📅 *Прогноз на сегодня:*\n"
        f"{payload['weather_desc']}\n"
        f"🌡️ Средняя температура: {temp_avg}°C\n"
        f"{wind_line}"
        f"💧 Осадки: {payload['precip']} мм\n\n"
        f"💪 Хорошего дня!"
    )
    return _append_minmax_stats(base, payload)


def format_evening_text(payload: dict) -> str:
    """Вечернее пожелание"""
    import random
    phrases = ["🌙 Спокойной ночи!", "✨ Доброй ночи!", "🌙 Сладких снов!"]
    sweet = ["Сны пусть будут радужными! 🌈", "Отдыхай! 💫", "До завтра! ⭐"]

    tomorrow_temp = "—"
    if payload.get('temp_min') is not None and payload.get('temp_max') is not None:
        tomorrow_temp = (payload['temp_min'] + payload['temp_max']) // 2

    wind = payload.get("wind_speed")
    wind_line = f"🌬️ Ветер сейчас: {wind} м/с\n" if wind is not None else ""

    base = (
        f"{random.choice(phrases)}\n\n"
        f"📊 *Сегодня:* {payload['temp_now']}°C, {payload['weather_desc']}\n"
        f"{wind_line}"
        f"💫 *Завтра:* ~{tomorrow_temp}°C\n\n"
        f"{random.choice(sweet)}"
    )
    return _append_minmax_stats(base, payload)


async def get_weather_text(payload: dict, text_type: str = "normal") -> str:
    """Получение текста погоды (Groq + fallback), в конце добавляет min/max статистику"""
    if groq_client:
        try:
            if text_type == "morning":
                system = (
                    "Ты доброе утро. Напиши короткое утреннее приветствие с прогнозом погоды. "
                    "Используй данные о погоде. Ответ должен быть тёплым и дружелюбным."
                )
                user = (
                    f"В {payload['location_short']} сегодня {payload['temp_min']}-{payload['temp_max']}°C, "
                    f"{payload['weather_desc']}, ветер {payload.get('wind_speed')} м/с, осадки {payload['precip']} мм."
                )
            elif text_type == "evening":
                system = (
                    "Ты нежный и заботливый. Напиши вечернее пожелание спокойной ночи. "
                    "Упомяни погоду сегодня и коротко на завтра. Добавь ласковые слова."
                )
                user = (
                    f"Сегодня было {payload['temp_now']}°C, {payload['weather_desc']}, "
                    f"ветер {payload.get('wind_speed')} м/с. Завтра {payload['temp_min']}-{payload['temp_max']}°C."
                )
            else:
                system = (
                    "Ты дружелюбный помощник. Дай прогноз погоды на сегодня. "
                    "Используй данные о температуре, осадках, ощущениях и ветре."
                )
                user = (
                    f"В {payload['location_short']} сейчас {payload['temp_now']}°C, {payload['weather_desc']}, "
                    f"ощущается как {payload['feels_like']}°C, ветер {payload.get('wind_speed')} м/с. "
                    f"Днем {payload['temp_min']}-{payload['temp_max']}°C, осадки {payload['precip']} мм."
                )

            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.7,
                max_tokens=220,
            )
            groq_text = completion.choices[0].message.content.strip()
            if groq_text and len(groq_text) > 20:
                logger.info(f"✅ Получен ответ от Groq для {text_type}")
                return _append_minmax_stats(groq_text, payload)
        except Exception as e:
            logger.error(f"❌ Ошибка Groq: {e}")

    logger.info(f"📝 Используем локальное форматирование для {text_type}")
    if text_type == "morning":
        return format_morning_text(payload)
    elif text_type == "evening":
        return format_evening_text(payload)
    else:
        return format_weather_text(payload)

# ================== ФУНКЦИИ НАПОМИНАНИЙ ==================
def parse_time(text: str) -> datetime | None:
    """Парсинг времени из текста с учетом московского времени"""
    now = datetime.now(MSK_TZ)
    text = text.lower().strip()

    match = re.search(r'через\s+(\d+)\s*(минут|минуты|минуту)', text)
    if match:
        minutes = int(match.group(1))
        minutes = max(1, min(minutes, 10080))
        return now + timedelta(minutes=minutes)

    if 'через минуту' in text:
        return now + timedelta(minutes=1)

    match = re.search(r'через\s+(\d+)\s*(час|часа|часов)', text)
    if match:
        hours = int(match.group(1))
        hours = min(hours, 168)
        return now + timedelta(hours=hours)

    if 'через час' in text:
        return now + timedelta(hours=1)

    match = re.search(r'послезавтра\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return (now + timedelta(days=2)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    if 'послезавтра' in text:
        return (now + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)

    match = re.search(r'завтра\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)

    match = re.search(r'завтра\s+в\s+(\d{1,2})', text)
    if match:
        hour = int(match.group(1))
        return (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)

    if 'завтра' in text:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    match = re.search(r'сегодня\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    match = re.search(r'(\d{1,2})\.(\d{1,2})\s+в\s+(\d{1,2}):(\d{2})', text)
    if match:
        day, month, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        year = now.year
        if month < now.month or (month == now.month and day < now.day):
            year += 1
        try:
            result = now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if result > now + timedelta(days=365):
                return None
            return result
        except ValueError:
            return None

    match = re.search(r'^(\d{1,2})\.(\d{1,2})$', text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = now.year
        if month < now.month or (month == now.month and day < now.day):
            year += 1
        try:
            result = now.replace(year=year, month=month, day=day, hour=9, minute=0, second=0, microsecond=0)
            if result > now + timedelta(days=365):
                return None
            return result
        except ValueError:
            return None

    match = re.search(r'^(\d{1,2}):(\d{2})$', text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > now else candidate + timedelta(days=1)

    return None


async def send_reminder(bot, user_id: int, text: str, reminder_id: int):
    """Отправка напоминания"""
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⏰ *НАПОМИНАНИЕ!*\n\n{text}",
            parse_mode='Markdown'
        )
        logger.info(f"✅ Напоминание {reminder_id} отправлено")

        if user_id in user_reminders:
            user_reminders[user_id] = [r for r in user_reminders[user_id] if r['id'] != reminder_id]
            save_reminders()

    except Exception as e:
        logger.error(f"❌ Ошибка отправки напоминания: {e}")

# ================== РАССЫЛКИ ==================
async def send_morning_forecast(bot):
    """Утренняя рассылка в 8:00"""
    now = datetime.now(MSK_TZ)
    logger.info(f"⏰ Утренняя рассылка в {now.strftime('%H:%M')}")

    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return

    for user_id, city in user_cities.items():
        try:
            geo = geocode_city(city)
            if not geo:
                continue
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            text = await get_weather_text(payload, "morning")
            await bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"❌ Ошибка утренней рассылки: {e}")


async def send_evening_message(bot):
    """Вечерняя рассылка в 22:00"""
    now = datetime.now(MSK_TZ)
    logger.info(f"🌙 Вечерняя рассылка в {now.strftime('%H:%M')}")

    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return

    for user_id, city in user_cities.items():
        try:
            geo = geocode_city(city)
            if not geo:
                continue
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            text = await get_weather_text(payload, "evening")
            await bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"❌ Ошибка вечерней рассылки: {e}")

# ================== ОБРАБОТЧИКИ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка /start"""
    user = update.effective_user
    user_id = user.id
    logger.info(f"👉 /start от @{user.username}")

    user_state[user_id] = "main"
    if user_id in context.user_data:
        context.user_data.clear()

    await update.message.reply_text(
        f"👋 *Привет, {user.first_name}!*\n\n"
        f"Я твой личный помощник. Что умею:\n"
        f"🌤️ *Погода* - узнай прогноз в любом городе\n"
        f"⏰ *Напоминания* - не дам забыть о важном\n"
        f"📝 *Заметки* - сохраняй свои мысли\n\n"
        f"Выбирай кнопку в меню!",
        reply_markup=main_keyboard,
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений"""
    text = update.message.text.strip()
    user = update.effective_user
    user_id = user.id

    logger.info(f"📨 Сообщение от @{user.username}: '{text}'")

    # ===== ГЛАВНЫЕ КНОПКИ =====
    if text == BTN_START:
        logger.info("🔴 Погода")
        user_state[user_id] = "main"
        if user_id in user_cities:
            del user_cities[user_id]
        await update.message.reply_text("Введи название города:", reply_markup=main_keyboard)
        return

    if text == BTN_UPDATE:
        logger.info("🟢 Обновить прогноз")
        user_state[user_id] = "main"
        if user_id not in user_cities:
            await update.message.reply_text("Сначала введи город!", reply_markup=main_keyboard)
            return
        await update.message.reply_text("🔄 Обновляю прогноз...", reply_markup=main_keyboard)
        await send_weather(update, user_cities[user_id])
        return

    if text == BTN_REMINDERS:
        logger.info("🔵 Напоминания")
        user_state[user_id] = "reminders"
        await update.message.reply_text(
            "📌 *Напоминания*\n\nВыбери действие:",
            parse_mode='Markdown',
            reply_markup=reminders_keyboard
        )
        return

    if text == BTN_NOTES:
        logger.info("📗 Заметки")
        user_state[user_id] = "notes"
        await update.message.reply_text(
            "📝 *Мои заметки*\n\nВыбери действие:",
            parse_mode='Markdown',
            reply_markup=notes_keyboard
        )
        return

    # ===== РЕЖИМ НАПОМИНАНИЙ =====
    if user_state.get(user_id) == "reminders":
        if text == "🔙 Назад":
            user_state[user_id] = "main"
            await update.message.reply_text("Главное меню:", reply_markup=main_keyboard)
            return

        if text == "📝 Создать":
            await update.message.reply_text(
                "🕐 *Создание напоминания*\n\n"
                "✨ *Форматы времени:*\n\n"
                "⏱️ *Относительные:*\n"
                "• `через 10 минут`\n"
                "• `через 2 часа`\n\n"
                "📅 *Даты:*\n"
                "• `завтра в 13:00`\n"
                "• `послезавтра в 13:00`\n"
                "• `18.02 в 13:00`\n\n"
                "📝 *Примеры:*\n"
                "• `Позвонить маме ! через 10 минут`\n"
                "• `Стоматолог ! 18.02 в 13:00`\n"
                "• `Забрать посылку ! завтра в 13:00`",
                parse_mode='Markdown'
            )
            context.user_data['awaiting_reminder'] = True
            return

        if text == "📋 Список":
            if user_id not in user_reminders or not user_reminders[user_id]:
                await update.message.reply_text("📋 У тебя нет напоминаний.", reply_markup=reminders_keyboard)
                return

            response = "📋 *Твои напоминания:*\n\n"
            for i, rem in enumerate(user_reminders[user_id], 1):
                rem_time = datetime.fromisoformat(rem['time'])
                if rem_time.tzinfo is None:
                    rem_time = MSK_TZ.localize(rem_time)
                t = rem_time.strftime("%d.%m.%Y %H:%M")
                response += f"{i}. 🕐 *{t}*\n   {rem['text']}\n\n"

            await update.message.reply_text(response, parse_mode='Markdown', reply_markup=reminders_keyboard)
            return

        if text == "❌ Удалить":
            if user_id not in user_reminders or not user_reminders[user_id]:
                await update.message.reply_text("Нет напоминаний.", reply_markup=reminders_keyboard)
                return
            kb = []
            for rem in user_reminders[user_id]:
                rem_time = datetime.fromisoformat(rem['time'])
                if rem_time.tzinfo is None:
                    rem_time = MSK_TZ.localize(rem_time)
                t = rem_time.strftime("%d.%m %H:%M")
                kb.append([f"❌ {t} - {rem['text'][:20]}"])
            kb.append(["🔙 Назад"])
            await update.message.reply_text(
                "Выбери для удаления:",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
            context.user_data['deleting_reminder'] = True
            return

        # ===== СОЗДАНИЕ НАПОМИНАНИЯ =====
        if context.user_data.get('awaiting_reminder'):
            logger.info(f"⏰ Создание напоминания: {text[:50]}...")

            if '!' not in text:
                await update.message.reply_text(
                    "❌ Используй формат: `Текст ! время`\n\n"
                    "Пример: `Позвонить маме ! через 10 минут`",
                    parse_mode='Markdown'
                )
                return

            parts = text.split('!')
            reminder_text = parts[0].strip()
            time_text = parts[1].strip()
            reminder_time = parse_time(time_text)

            if not reminder_time:
                await update.message.reply_text(
                    "❌ Не понял время. Попробуй:\n"
                    "• `через 10 минут`\n"
                    "• `завтра в 13:00`\n"
                    "• `послезавтра в 13:00`\n"
                    "• `18.02 в 13:00`",
                    parse_mode='Markdown'
                )
                return

            now = datetime.now(MSK_TZ)
            if reminder_time < now + timedelta(minutes=1):
                reminder_time = now + timedelta(minutes=1)
                await update.message.reply_text("⏳ Минимальное время - 1 минута. Устанавливаю на 1 минуту.")

            if reminder_time > now + timedelta(days=365):
                await update.message.reply_text("⏳ Максимальное время - 1 год. Устанавливаю на 1 год.")
                reminder_time = now + timedelta(days=365)

            global reminder_counter
            reminder_counter += 1

            global scheduler
            if scheduler:
                job = scheduler.add_job(
                    send_reminder,
                    'date',
                    run_date=reminder_time,
                    args=[context.application.bot, user_id, reminder_text, reminder_counter]
                )

                if user_id not in user_reminders:
                    user_reminders[user_id] = []

                user_reminders[user_id].append({
                    'id': reminder_counter,
                    'text': reminder_text,
                    'time': reminder_time.isoformat(),
                    'job_id': job.id
                })

                save_reminders()

                context.user_data['awaiting_reminder'] = False
                await update.message.reply_text(
                    f"✅ *Напоминание создано!*\n\n📝 {reminder_text}\n🕐 {reminder_time.strftime('%d.%m.%Y %H:%M')}",
                    parse_mode='Markdown',
                    reply_markup=reminders_keyboard
                )
            else:
                await update.message.reply_text("❌ Ошибка планировщика. Попробуй позже.")
            return

        # ===== УДАЛЕНИЕ НАПОМИНАНИЯ =====
        if context.user_data.get('deleting_reminder'):
            if text == "🔙 Назад":
                context.user_data['deleting_reminder'] = False
                await update.message.reply_text("Меню напоминаний:", reply_markup=reminders_keyboard)
                return

            if user_id in user_reminders:
                for rem in user_reminders[user_id][:]:
                    rem_time = datetime.fromisoformat(rem['time'])
                    if rem_time.tzinfo is None:
                        rem_time = MSK_TZ.localize(rem_time)
                    preview = f"❌ {rem_time.strftime('%d.%m %H:%M')} - {rem['text'][:20]}"
                    if preview == text:
                        try:
                            if scheduler:
                                scheduler.remove_job(rem['job_id'])
                        except Exception:
                            pass
                        user_reminders[user_id].remove(rem)
                        save_reminders()
                        await update.message.reply_text("✅ Удалено!", reply_markup=reminders_keyboard)
                        context.user_data['deleting_reminder'] = False
                        return

            await update.message.reply_text("❌ Не найдено", reply_markup=reminders_keyboard)
            context.user_data['deleting_reminder'] = False
            return

        await update.message.reply_text("Используй кнопки меню напоминаний.", reply_markup=reminders_keyboard)
        return

    # ===== РЕЖИМ УДАЛЕНИЯ ЗАМЕТОК (ФИКС) =====
    if user_state.get(user_id) == "deleting_note":
        if text == "🔙 Назад":
            context.user_data['deleting_note'] = False
            user_state[user_id] = "notes"
            await update.message.reply_text("Меню заметок:", reply_markup=notes_keyboard)
            return

        if user_id in user_notes:
            for note in user_notes[user_id][:]:
                note_date = datetime.fromisoformat(note['date']).strftime("%d.%m")
                preview = note['text'][:30]
                if f"❌ {note_date} - {preview}" == text:
                    user_notes[user_id].remove(note)
                    save_notes()
                    await update.message.reply_text("✅ Заметка удалена!", reply_markup=notes_keyboard)
                    context.user_data['deleting_note'] = False
                    user_state[user_id] = "notes"
                    return

        await update.message.reply_text("❌ Не найдено", reply_markup=notes_keyboard)
        context.user_data['deleting_note'] = False
        user_state[user_id] = "notes"
        return

    # ===== РЕЖИМ ЗАМЕТОК =====
    if user_state.get(user_id) == "notes":
        if text == "🔙 Назад":
            user_state[user_id] = "main"
            await update.message.reply_text("Главное меню:", reply_markup=main_keyboard)
            return

        if text == "📝 Новая заметка":
            user_state[user_id] = "new_note"
            await update.message.reply_text(
                "📝 *Новая заметка*\n\nПросто напиши текст заметки:",
                parse_mode='Markdown'
            )
            return

        if text == "📋 Все заметки":
            if user_id not in user_notes or not user_notes[user_id]:
                await update.message.reply_text("📭 У тебя пока нет заметок.", reply_markup=notes_keyboard)
                return

            response = "📚 *Твои заметки:*\n\n"
            for i, note in enumerate(reversed(user_notes[user_id][-10:]), 1):
                note_date = datetime.fromisoformat(note['date']).strftime("%d.%m")
                response += f"{i}. 📝 *{note_date}*\n   {note['text'][:100]}...\n\n"

            response += "_Показаны последние 10 заметок_"
            await update.message.reply_text(response, parse_mode='Markdown', reply_markup=notes_keyboard)
            return

        if text == "❌ Удалить заметку":
            if user_id not in user_notes or not user_notes[user_id]:
                await update.message.reply_text("Нет заметок.", reply_markup=notes_keyboard)
                return

            kb = []
            for note in reversed(user_notes[user_id][-5:]):
                note_date = datetime.fromisoformat(note['date']).strftime("%d.%m")
                preview = note['text'][:30]
                kb.append([f"❌ {note_date} - {preview}"])
            kb.append(["🔙 Назад"])

            user_state[user_id] = "deleting_note"
            context.user_data['deleting_note'] = True

            await update.message.reply_text(
                "Выбери заметку для удаления (последние 5):",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
            return

        await update.message.reply_text("Используй кнопки меню заметок.", reply_markup=notes_keyboard)
        return

    # ===== СОЗДАНИЕ НОВОЙ ЗАМЕТКИ =====
    if user_state.get(user_id) == "new_note":
        logger.info(f"📝 Новая заметка от @{user.username}: {text[:50]}...")

        global notes_counter
        notes_counter += 1

        if user_id not in user_notes:
            user_notes[user_id] = []

        user_notes[user_id].append({
            'id': notes_counter,
            'text': text,
            'date': datetime.now(MSK_TZ).isoformat()
        })

        save_notes()

        user_state[user_id] = "notes"
        await update.message.reply_text(
            "✅ *Заметка сохранена!*",
            parse_mode='Markdown',
            reply_markup=notes_keyboard
        )
        return

    # ===== ВВОД ГОРОДА (ПО УМОЛЧАНИЮ) =====
    logger.info(f"🏙️ Ввод города: {text}")
    user_state[user_id] = "main"
    user_cities[user_id] = text
    await update.message.reply_text(f"🔍 Ищу погоду для {text}...", reply_markup=main_keyboard)
    await send_weather(update, text)


async def send_weather(update: Update, city: str):
    """Отправка прогноза"""
    try:
        geo = geocode_city(city)
        if not geo:
            await update.message.reply_text(f"❌ Город '{city}' не найден.", reply_markup=main_keyboard)
            return

        wx = fetch_today_weather(geo["latitude"], geo["longitude"])
        payload = build_weather_payload(geo.get("name", city), geo, wx)
        text = await get_weather_text(payload, "normal")

        await update.message.reply_text(text, reply_markup=main_keyboard, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"❌ Ошибка погоды: {e}")
        await update.message.reply_text("❌ Ошибка. Попробуй позже.", reply_markup=main_keyboard)

# ================== ЗАПУСК ==================
async def main():
    global scheduler
    logger.info("🚀 Запуск бота...")

    load_reminders()
    load_notes()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    scheduler = AsyncIOScheduler(timezone=str(MSK_TZ))
    scheduler.add_job(send_morning_forecast, CronTrigger(hour=8, minute=0, timezone=MSK_TZ), args=[app.bot])
    scheduler.add_job(send_evening_message, CronTrigger(hour=22, minute=0, timezone=MSK_TZ), args=[app.bot])
    scheduler.start()

    restored = 0
    for user_id, reminders in user_reminders.items():
        for rem in reminders[:]:
            try:
                reminder_time = datetime.fromisoformat(rem['time'])
                if reminder_time.tzinfo is None:
                    reminder_time = MSK_TZ.localize(reminder_time)

                if reminder_time > datetime.now(MSK_TZ):
                    job = scheduler.add_job(
                        send_reminder,
                        'date',
                        run_date=reminder_time,
                        args=[app.bot, user_id, rem['text'], rem['id']],
                        id=rem['job_id']
                    )
                    rem['job_id'] = job.id
                    restored += 1
                else:
                    user_reminders[user_id].remove(rem)
            except Exception as e:
                logger.error(f"❌ Ошибка восстановления: {e}")

    logger.info(f"🔄 Восстановлено напоминаний: {restored}")
    save_reminders()
    logger.info("✅ Бот запущен! Планировщик работает.")

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
