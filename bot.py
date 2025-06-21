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
            context = "\n".join(f"User: {msg[0]}\nBot: {msg[1]}" for msg in history) if history else "Нет истории"
            
            user_facts = []
            with get_db_connection() as conn:
                cursor = conn.execute("SELECT fact, value FROM user_facts WHERE user_id = ?", (user_id,))
                user_facts = cursor.fetchall()
            
            facts = "\n".join(f"{fact[0]}: {fact[1]}" for fact in user_facts) or "Ничего не известно"
            
            system_prompt = f"""Ты — Дед Коля (67 лет). Отвечай как матерый старик:
            - Ругайся: курва, ебать в рот, блядь
            - Добавляй сарказм и чёрный юмор
            - Упоминай свой трактор и Опель Астру
            
            Факты о собеседнике:
            {facts}
            
            История диалога:
            {context}"""
            
            response = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": WEBHOOK_URL or "https://ded-kolia-bot.com",
                    "X-Title": "Дед Коля Бот"
                },
                json={
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ],
                    "temperature": 0.85,
                    "max_tokens": 350
                },
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            return None
            
        except Exception as e:
            logger.warning(f"Нейросеть недоступна: {str(e)}")
            return None

# Инициализация систем
ded_kolia = DedKolia()

# ====================== TELEGRAM ОБРАБОТЧИКИ ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(f"👴 Дед Коля на связи! Используем модель: {MODEL_NAME}\nШо надо?")
    except Exception as e:
        logger.error(f"Ошибка в команде /start: {str(e)}")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if not context.args:
            await update.message.reply_text("Чё запоминать-то? Используй: /remember я люблю пиво")
            return
        
        fact_text = " ".join(context.args)
        with get_db_connection() as conn:
            conn.execute("""
                INSERT INTO user_facts (user_id, fact, value, timestamp)
                VALUES (?, 'факт', ?, ?)
            """, (user_id, fact_text, datetime.now().isoformat()))
        await update.message.reply_text(f"✅ Окей, курва, запомнил: {fact_text}")
    except Exception as e:
        logger.error(f"Ошибка в команде /remember: {str(e)}")
        await update.message.reply_text("Блядь, не запомнилось... Давай ещё раз?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        user_input = update.message.text
        
        response = ded_kolia.generate_response(user_id, user_input)
        ded_kolia.save_interaction(user_id, user_input, response)
        
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Ой, курва, я сломался... Попробуй ещё раз!")

# ====================== ИНИЦИАЛИЗАЦИЯ TELEGRAM ======================
def init_telegram():
    global telegram_app
    if telegram_app is None:
        try:
            telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
            telegram_app.add_handler(CommandHandler("start", start))
            telegram_app.add_handler(CommandHandler("remember", remember_command))
            telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            # Инициализация в цикле событий
            loop.run_until_complete(telegram_app.initialize())
            loop.run_until_complete(telegram_app.start())
            
            logger.info("Telegram бот инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации Telegram: {str(e)}")
            raise

# ====================== FLASK РОУТЫ ======================
@app.route('/')
def home():
    return f"🤖 Дед Коля в работе! Модель: {MODEL_NAME}"

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    try:
        if not WEBHOOK_URL:
            return jsonify({"status": "error", "message": "WEBHOOK_URL не настроен"}), 400
        
        init_telegram()
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        
        loop.run_until_complete(telegram_app.bot.set_webhook(webhook_url))
        logger.info(f"Вебхук установлен: {webhook_url}")
        
        return jsonify({
            "status": "success",
            "message": f"Вебхук установлен: {webhook_url}",
            "bot_info": {
                "username": telegram_app.bot.username,
                "id": telegram_app.bot.id
            }
        }), 200
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        if telegram_app is None:
            init_telegram()
        
        update = Update.de_json(request.json, telegram_app.bot)
        loop.run_until_complete(telegram_app.process_update(update))
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/test', methods=['GET'])
def test():
    try:
        test_cases = [
            ("Привет", "приветствие"),
            ("Как меня зовут?", "факты"),
            ("Что ты помнишь?", "история")
        ]
        
        results = []
        test_user = "test_user"
        
        for message, test_type in test_cases:
            response = ded_kolia.generate_response(test_user, message)
            results.append({
                "test": test_type,
                "message": message,
                "response": response
            })
        
        return jsonify({
            "status": "success",
            "database": "работает",
            "ai_available": bool(OPENROUTER_API_KEY),
            "tests": results
        }), 200
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# ====================== ЗАПУСК ======================
def check_env_vars():
    required = [
        ('TELEGRAM_TOKEN', TELEGRAM_TOKEN),
        ('WEBHOOK_URL', WEBHOOK_URL)
    ]
    
    missing = [name for name, val in required if not val]
    if missing:
        logger.error(f"Отсутствуют обязательные переменные: {', '.join(missing)}")
        return False
    
    logger.info("="*50)
    logger.info(f"TELEGRAM_TOKEN: {'установлен' if TELEGRAM_TOKEN else 'отсутствует'}")
    logger.info(f"OPENROUTER_API_KEY: {'установлен' if OPENROUTER_API_KEY else 'отсутствует'}")
    logger.info(f"WEBHOOK_URL: {WEBHOOK_URL}")
    logger.info(f"PORT: {PORT}")
    logger.info(f"МОДЕЛЬ: {MODEL_NAME}")
    logger.info("="*50)
    return True

if __name__ == '__main__':
    if not check_env_vars():
        exit(1)
    
    try:
        init_telegram()
        
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
            loop.run_until_complete(telegram_app.bot.set_webhook(webhook_url))
            logger.info(f"🚀 Вебхук установлен: {webhook_url}")
        
        logger.info(f"🤖 Запускаем сервер на порту {PORT}...")
        app.run(host='0.0.0.0', port=PORT)
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
    finally:
        if telegram_app:
            loop.run_until_complete(telegram_app.stop())
            loop.run_until_complete(telegram_app.shutdown())
        loop.close()
