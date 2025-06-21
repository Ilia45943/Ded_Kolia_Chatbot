import os
import logging
import sqlite3
import requests
import re
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from flask import Flask, request, jsonify

# ====================== НАСТРОЙКА ======================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')
PORT = int(os.environ.get('PORT', 10000))
MODEL_NAME = "deepseek/deepseek-r1-0528-qwen3-8b:free"

# Инициализация Flask
app = Flask(__name__)

# Глобальные переменные
telegram_app = None
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# ====================== БАЗА ДАННЫХ ======================
def get_db_connection(db_path=":memory:"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                fact TEXT NOT NULL,
                value TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                user_message TEXT,
                bot_response TEXT
            )
        """)

# ====================== ЛОГИКА БОТА ======================
class DedKolia:
    def __init__(self):
        init_db()
        self.default_responses = [
            "Чёрт, нейросеть глючит... Ну ладно, без неё обойдёмся!",
            "Эх, сейчас ИИ не отвечает... Давай просто поболтаем?",
            "Курва, техника подводит! Ну расскажи, как дела?",
            "Блядь, нейросеть тупит... А ты как сам?"
        ]

    def _extract_facts(self, user_id, text):
        try:
            patterns = {
                "имя": r"(меня зовут|мое имя|зовут меня) ([а-яА-ЯёЁ]+)",
                "город": r"(я из|живу в|город) ([а-яА-ЯёЁ\s]+)"
            }
            
            for fact_type, pattern in patterns.items():
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = match.group(2).strip()
                    with get_db_connection() as conn:
                        conn.execute("""
                            INSERT INTO user_facts (user_id, fact, value, timestamp)
                            VALUES (?, ?, ?, ?)
                        """, (user_id, fact_type, value, datetime.now().isoformat()))
                    return f"Окей, запомнил что твоё {fact_type} - {value}!"
            return None
        except Exception as e:
            logger.error(f"Ошибка извлечения фактов: {str(e)}")
            return None

    def get_history(self, user_id, limit=3):
        try:
            with get_db_connection() as conn:
                cursor = conn.execute("""
                    SELECT user_message, bot_response FROM sessions 
                    WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
                """, (user_id, limit))
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Ошибка получения истории: {str(e)}")
            return []

    def save_interaction(self, user_id, user_message, bot_response):
        try:
            with get_db_connection() as conn:
                conn.execute("""
                    INSERT INTO sessions (user_id, timestamp, user_message, bot_response)
                    VALUES (?, ?, ?, ?)
                """, (user_id, datetime.now().isoformat(), user_message, bot_response))
        except Exception as e:
            logger.error(f"Ошибка сохранения диалога: {str(e)}")

    def generate_response(self, user_id, message):
        try:
            # Сначала пробуем извлечь факты
            fact_response = self._extract_facts(user_id, message)
            if fact_response:
                return fact_response
            
            # Пробуем получить ответ от нейросети
            response = self._try_ai_response(user_id, message)
            if response:
                return response
            
            # Если нейросеть недоступна - используем резервные ответы
            history = self.get_history(user_id)
            if history:
                return "Я всё помню, но нейросеть сейчас не отвечает. Давай обсудим что-то ещё?"
            
            return self.default_responses[hash(user_id) % len(self.default_responses)]
            
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {str(e)}")
            return "Ой, что-то пошло не так... Давай попробуем ещё раз?"

    def _try_ai_response(self, user_id, message):
        if not OPENROUTER_API_KEY:
            return None
            
        try:
            history = self.get_history(user_id)
            context = "\n".join(f"User: {msg[0]}\nBot: {msg[1
