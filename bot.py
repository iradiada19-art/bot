#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bot.py - С НАПОМИНАНИЯМИ

import os
import asyncio
import logging
import requests
from groq import Groq
from datetime import datetime, time, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
import re

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
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
BTN_START = "Старт"
BTN_UPDATE = "Обновить прогноз"
BTN_REMINDERS = "Мои напоминания"

# Состояния для ConversationHandler напоминаний
(
    REMINDER_MENU,
    REMINDER_TEXT,
    REMINDER_TIME,
    REMINDER_CONFIRM,
    REMINDER_DELETE,
) = range(5)

# Создаем клавиатуру
main_keyboard = ReplyKeyboardMarkup(
    [[BTN_START, BTN_UPDATE], [BTN_REMINDERS]],
    resize_keyboard=True,
)

# Инициализация Groq клиента
groq_client = Groq(api_key=GROQ_API_KEY)

# Словарь для хранения городов пользователей
user_cities = {}

# Словарь для хранения напоминаний пользователей
# Структура: {user_id: [{"id": 1, "text": "...", "time": "2024-01-01 12:00", "job_id": "..."}]}
user_reminders = {}

# Счетчик для ID напоминаний
reminder_counter = 0

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
    
    # Короткий прогноз на завтра
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

# ================== ФУНКЦИИ НАПОМИНАНИЙ ==================
def parse_time(text: str) -> datetime | None:
    """Парсинг времени из текста"""
    text = text.lower().strip()
    now = datetime.now()
    
    # Шаблоны времени
    patterns = [
        # Сегодня в 15:30
        (r'сегодня\s+в\s+(\d{1,2}):(\d{2})', lambda h, m: now.replace(hour=int(h), minute=int(m), second=0)),
        # Завтра в 9 утра
        (r'завтра\s+в\s+(\d{1,2})\s*(?:час|часа|часов)?\s*(?:утра|дня|вечера)?', 
         lambda h: (now + timedelta(days=1)).replace(hour=int(h), minute=0, second=0)),
        # Через 2 часа
        (r'через\s+(\d+)\s*(?:час|часа|часов)', lambda h: now + timedelta(hours=int(h))),
        # Через 30 минут
        (r'через\s+(\d+)\s*(?:минут|минуты|минуту)', lambda m: now + timedelta(minutes=int(m))),
        # 15:30 (сегодня если время еще не прошло, иначе завтра)
        (r'^(\d{1,2}):(\d{2})$', lambda h, m: parse_time_short(now, int(h), int(m))),
    ]
    
    for pattern, handler in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                if len(match.groups()) == 2 and pattern != r'^(\d{1,2}):(\d{2})$':
                    return handler(match.group(1), match.group(2))
                elif len(match.groups()) == 1:
                    return handler(match.group(1))
                else:
                    return handler(*match.groups())
            except (ValueError, TypeError) as e:
                logger.error(f"Ошибка парсинга времени: {e}")
                continue
    
    return None

def parse_time_short(now: datetime, hour: int, minute: int) -> datetime:
    """Парсинг короткого формата времени (15:30)"""
    candidate = now.replace(hour=hour, minute=minute, second=0)
    if candidate > now:
        return candidate
    else:
        return candidate + timedelta(days=1)

async def send_reminder(bot, user_id: int, reminder_text: str, reminder_id: int):
    """Отправка напоминания"""
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⏰ *НАПОМИНАНИЕ!*\n\n{reminder_text}",
            parse_mode='Markdown'
        )
        logger.info(f"✅ Напоминание {reminder_id} отправлено пользователю {user_id}")
        
        # Удаляем напоминание из словаря после отправки
        if user_id in user_reminders:
            user_reminders[user_id] = [r for r in user_reminders[user_id] if r['id'] != reminder_id]
            
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке напоминания {reminder_id}: {e}")

# ================== ОБРАБОТЧИКИ НАПОМИНАНИЙ ==================
async def reminders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню напоминаний"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if text == BTN_REMINDERS:
        keyboard = [
            [InlineKeyboardButton("📝 Создать напоминание", callback_data="create_reminder")],
            [InlineKeyboardButton("📋 Мои напоминания", callback_data="list_reminders")],
            [InlineKeyboardButton("❌ Удалить напоминание", callback_data="delete_reminder")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "📌 *Управление напоминаниями*\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return REMINDER_MENU
    
    return ConversationHandler.END

async def reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок в меню напоминаний"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "create_reminder":
        await query.edit_message_text(
            "📝 *Создание напоминания*\n\n"
            "Напишите текст напоминания:",
            parse_mode='Markdown'
        )
        return REMINDER_TEXT
    
    elif query.data == "list_reminders":
        if user_id not in user_reminders or not user_reminders[user_id]:
            await query.edit_message_text(
                "📋 У вас нет активных напоминаний.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data="back_to_reminders")
                ]])
            )
            return REMINDER_MENU
        
        reminders = user_reminders[user_id]
        text = "📋 *Ваши напоминания:*\n\n"
        for i, rem in enumerate(reminders, 1):
            rem_time = datetime.fromisoformat(rem['time']).strftime("%d.%m.%Y %H:%M")
            text += f"{i}. 🕐 *{rem_time}*\n   {rem['text']}\n\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="back_to_reminders")
            ]])
        )
        return REMINDER_MENU
    
    elif query.data == "delete_reminder":
        if user_id not in user_reminders or not user_reminders[user_id]:
            await query.edit_message_text(
                "📋 У вас нет активных напоминаний для удаления.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data="back_to_reminders")
                ]])
            )
            return REMINDER_MENU
        
        # Создаем клавиатуру с напоминаниями для удаления
        reminders = user_reminders[user_id]
        keyboard = []
        for rem in reminders:
            rem_time = datetime.fromisoformat(rem['time']).strftime("%d.%m %H:%M")
            keyboard.append([InlineKeyboardButton(
                f"{rem_time} - {rem['text'][:20]}...",
                callback_data=f"del_{rem['id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_reminders")])
        
        await query.edit_message_text(
            "❌ *Выберите напоминание для удаления:*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return REMINDER_DELETE
    
    elif query.data.startswith("del_"):
        reminder_id = int(query.data.replace("del_", ""))
        
        if user_id in user_reminders:
            # Находим напоминание
            reminder_to_delete = None
            for rem in user_reminders[user_id]:
                if rem['id'] == reminder_id:
                    reminder_to_delete = rem
                    break
            
            if reminder_to_delete:
                # Удаляем из планировщика
                if 'job_id' in reminder_to_delete:
                    try:
                        context.application.scheduler.remove_job(reminder_to_delete['job_id'])
                    except:
                        pass
                
                # Удаляем из словаря
                user_reminders[user_id] = [r for r in user_reminders[user_id] if r['id'] != reminder_id]
                
                await query.edit_message_text(
                    "✅ Напоминание удалено!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 К напоминаниям", callback_data="back_to_reminders")
                    ]])
                )
            else:
                await query.edit_message_text(
                    "❌ Напоминание не найдено.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data="back_to_reminders")
                    ]])
                )
        return REMINDER_MENU
    
    elif query.data == "back_to_reminders":
        # Возврат в меню напоминаний
        keyboard = [
            [InlineKeyboardButton("📝 Создать напоминание", callback_data="create_reminder")],
            [InlineKeyboardButton("📋 Мои напоминания", callback_data="list_reminders")],
            [InlineKeyboardButton("❌ Удалить напоминание", callback_data="delete_reminder")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        await query.edit_message_text(
            "📌 *Управление напоминаниями*\n\n"
            "Выберите действие:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return REMINDER_MENU
    
    elif query.data == "back_to_main":
        await query.edit_message_text(
            "👋 Возврат в главное меню",
            reply_markup=None
        )
        return ConversationHandler.END
    
    return REMINDER_MENU

async def reminder_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение текста напоминания"""
    text = update.message.text
    context.user_data['reminder_text'] = text
    
    await update.message.reply_text(
        "🕐 *Когда напомнить?*\n\n"
        "Напишите время в одном из форматов:\n"
        "• `15:30` (сегодня или завтра)\n"
        "• `сегодня в 15:30`\n"
        "• `завтра в 9`\n"
        "• `через 2 часа`\n"
        "• `через 30 минут`",
        parse_mode='Markdown'
    )
    return REMINDER_TIME

async def reminder_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение времени напоминания"""
    time_text = update.message.text
    reminder_time = parse_time(time_text)
    
    if not reminder_time:
        await update.message.reply_text(
            "❌ Не понял время. Попробуйте еще раз:\n"
            "• `15:30`\n"
            "• `сегодня в 15:30`\n"
            "• `завтра в 9`\n"
            "• `через 2 часа`",
            parse_mode='Markdown'
        )
        return REMINDER_TIME
    
    # Сохраняем время
    context.user_data['reminder_time'] = reminder_time.isoformat()
    
    # Показываем подтверждение
    reminder_text = context.user_data.get('reminder_text', '')
    time_str = reminder_time.strftime("%d.%m.%Y в %H:%M")
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_reminder")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_reminder")]
    ]
    
    await update.message.reply_text(
        f"📝 *Проверьте данные:*\n\n"
        f"Текст: {reminder_text}\n"
        f"Время: {time_str}\n\n"
        f"Всё верно?",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return REMINDER_CONFIRM

async def reminder_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение создания напоминания"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_reminder":
        user_id = query.from_user.id
        reminder_text = context.user_data.get('reminder_text', '')
        time_iso = context.user_data.get('reminder_time')
        
        if not reminder_text or not time_iso:
            await query.edit_message_text("❌ Ошибка: данные потеряны. Попробуйте снова.")
            return ConversationHandler.END
        
        reminder_time = datetime.fromisoformat(time_iso)
        
        # Создаем ID напоминания
        global reminder_counter
        reminder_counter += 1
        reminder_id = reminder_counter
        
        # Создаем задачу в планировщике
        job = context.application.scheduler.add_job(
            send_reminder,
            DateTrigger(run_date=reminder_time),
            args=[context.application.bot, user_id, reminder_text, reminder_id],
            id=f"reminder_{user_id}_{reminder_id}"
        )
        
        # Сохраняем напоминание в словарь
        if user_id not in user_reminders:
            user_reminders[user_id] = []
        
        user_reminders[user_id].append({
            'id': reminder_id,
            'text': reminder_text,
            'time': time_iso,
            'job_id': job.id
        })
        
        time_str = reminder_time.strftime("%d.%m.%Y в %H:%M")
        await query.edit_message_text(
            f"✅ *Напоминание создано!*\n\n"
            f"Текст: {reminder_text}\n"
            f"Время: {time_str}\n\n"
            f"Я напомню вам в это время.",
            parse_mode='Markdown'
        )
        
        # Очищаем данные
        context.user_data.pop('reminder_text', None)
        context.user_data.pop('reminder_time', None)
        
    elif query.data == "cancel_reminder":
        await query.edit_message_text("❌ Создание напоминания отменено.")
    
    return ConversationHandler.END

# ================== ОБРАБОТЧИКИ РАССЫЛКИ ==================
async def send_morning_forecast(bot, scheduler):
    """Утренняя рассылка в 8:00"""
    logger.info("⏰ Запуск утренней рассылки")
    
    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return
    
    for user_id, city in user_cities.items():
        try:
            logger.info(f"Отправляем утренний прогноз для пользователя {user_id}, город {city}")
            
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            text = await get_groq_weather(payload, "morning")
            
            if not text:
                text = format_morning_text(payload)
            
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode='Markdown'
            )
            logger.info(f"✅ Утренний прогноз отправлен пользователю {user_id}")
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке утреннего прогноза пользователю {user_id}: {e}")

async def send_evening_message(bot, scheduler):
    """Вечерняя рассылка в 22:00"""
    logger.info("🌙 Запуск вечерней рассылки")
    
    if not user_cities:
        logger.info("Нет пользователей для рассылки")
        return
    
    for user_id, city in user_cities.items():
        try:
            logger.info(f"Отправляем вечернее пожелание пользователю {user_id}")
            
            geo = geocode_city(city)
            if not geo:
                continue
                
            wx = fetch_today_weather(geo["latitude"], geo["longitude"])
            payload = build_weather_payload(geo.get("name", city), geo, wx)
            
            text = await get_groq_weather(payload, "evening")
            
            if not text:
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

# ================== ОСНОВНЫЕ ОБРАБОТЧИКИ ==================
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
        f"⏰ *Бонус:* Я буду сам присылать прогноз в 8:00 и желать спокойной ночи в 22:00!\n"
        f"📌 *Новое:* Теперь я могу создавать напоминания!",
        reply_markup=main_keyboard,
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
            reply_markup=main_keyboard
        )
        return
    
    elif text == BTN_UPDATE:
        logger.info(f"🟢 НАЖАТА КНОПКА: Обновить прогноз")
        if user_id not in user_cities:
            await update.message.reply_text(
                "Сначала введите название города!",
                reply_markup=main_keyboard
            )
            return
        
        city = user_cities[user_id]
        logger.info(f"🔄 Обновляем прогноз для города: {city}")
        await update.message.reply_text(
            f"🔄 Обновляю прогноз для *{city}*...",
            reply_markup=main_keyboard,
            parse_mode='Markdown'
        )
        await send_weather(update, city)
        return
    
    elif text == BTN_REMINDERS:
        # Передаем в ConversationHandler напоминаний
        await reminders_menu(update, context)
        return
    
    # ===== ОБРАБОТКА ВВОДА ГОРОДА =====
    logger.info(f"🏙️ Ввод города: {text}")
    
    # Сохраняем город
    user_cities[user_id] = text
    
    await update.message.reply_text(
        f"🔍 Ищу погоду для *{text}*...",
        reply_markup=main_keyboard,
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
                reply_markup=main_keyboard,
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
            reply_markup=main_keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
