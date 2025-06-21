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

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')  # Убедимся, что нет слеша в конце
PORT = int(os.environ.get('PORT', 10000))
MODEL_NAME = "DeepSeek R1 0528 Qwen 3.8B"

# Инициализация Flask
app = Flask(__name__)

# Глобальная переменная для приложения Telegram
telegram_app = None

# ====================== БАЗА ЗНАНИЙ ======================
class KnowledgeBase:
    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    fact TEXT NOT NULL,
                    value TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
    
    def add_user_fact(self, user_id, fact, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO user_facts (user_id, fact, value, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, fact, value, datetime.now().isoformat()))
    
    def get_user_facts(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT fact, value FROM user_facts WHERE user_id = ?
            """, (user_id,))
            return cursor.fetchall()

# ====================== ПАМЯТЬ ДИАЛОГОВ ======================
class Memory:
    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    user_message TEXT,
                    bot_response TEXT
                )
            """)
    
    def get_history(self, user_id, limit=3):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT user_message, bot_response FROM sessions 
                WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (user_id, limit))
            return cursor.fetchall()
    
    def save_interaction(self, user_id, user_message, bot_response):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO sessions (user_id, timestamp, user_message, bot_response)
                VALUES (?, ?, ?, ?)
            """, (user_id, datetime.now().isoformat(), user_message, bot_response))

# ====================== ЛОГИКА ДЕДА КОЛИ ======================
class DedKolia:
    def __init__(self, knowledge_base, memory):
        self.kb = knowledge_base
        self.memory = memory
    
    def _extract_facts(self, user_id, text):
        patterns = {
            "имя": r"(меня зовут|мое имя|зовут меня) ([а-яА-ЯёЁ]+)",
            "город": r"(я из|живу в|город) ([а-яА-ЯёЁ\s]+)"
        }
        
        for fact_type, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = match.group(2).strip()
                self.kb.add_user_fact(user_id, fact_type, value)
                return f"Окей, запомнил что твоё {fact_type} - {value}!"
        return None
    
    def generate_response(self, user_id, message):
        fact_response = self._extract_facts(user_id, message)
        if fact_response:
            return fact_response
        
        history = self.memory.get_history(user_id)
        context = "\n".join(
            f"User: {msg[0]}\nДед Коля: {msg[1]}" 
            for msg in history
        ) if history else "Нет истории"
        
        user_facts = "\n".join(
            f"{fact[0]}: {fact[1]}" 
            for fact in self.kb.get_user_facts(user_id)
        ) or "Ничего не известно"
        
        system_prompt = f"""Ты — Дед Коля (67 лет). Отвечай как матерый старик:
        - Ругайся: курва, ебать в рот, блядь
        - Добавляй сарказм и чёрный юмор
        - Упоминай свой трактор и Опель Астру
        - Любимые темы: бухло, Люда, Федя
        
        Факты о собеседнике:
        {user_facts}
        
        История диалога:
        {context}"""
        
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": WEBHOOK_URL or "https://ded-kolia-bot.com",
            "X-Title": "Дед Коля Бот"
        }
        payload = {
            "model": "deepseek/deepseek-r1:free",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            "temperature": 0.85,
            "max_tokens": 350
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                logger.error(f"API error: {response.status_code} - {response.text}")
                return "Ой, курва, что-то сломалось... Попробуй позже!"
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            return "Чёрт, сломалось! Давай ещё раз попробуем."

# Инициализация систем (ПЕРЕМЕЩЕНО В ГЛОБАЛЬНУЮ ОБЛАСТЬ)
knowledge_base = KnowledgeBase()
memory = Memory()
ded_kolia = DedKolia(knowledge_base, memory)

# Telegram обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👴 Дед Коля на связи! Используем модель: {MODEL_NAME}\nШо надо?")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if not context.args:
        await update.message.reply_text("Чё запоминать-то? Используй: /remember я люблю пиво")
        return
    
    fact_text = " ".join(context.args)
    knowledge_base.add_user_fact(user_id, "факт", fact_text)
    await update.message.reply_text(f"✅ Окей, курва, запомнил: {fact_text}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_input = update.message.text
    response = ded_kolia.generate_response(user_id, user_input)
    memory.save_interaction(user_id, user_input, response)
    await update.message.reply_text(response)

# Инициализация приложения Telegram
def init_telegram_app():
    global telegram_app
    if not telegram_app:
        telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("remember", remember_command))
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return telegram_app

# Flask роуты
@app.route('/')
def home():
    return f"🤖 Дед Коля в работе! Модель: {MODEL_NAME}"

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    try:
        if not WEBHOOK_URL:
            return jsonify({"status": "error", "message": "WEBHOOK_URL not configured"}), 500
        
        # Инициализируем приложение Telegram, если еще не инициализировано
        if not telegram_app:
            init_telegram_app()
        
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        # Используем глобальное приложение вместо создания нового
        asyncio.run(telegram_app.bot.set_webhook(webhook_url))
        return jsonify({
            "status": "success",
            "message": f"Webhook set to {webhook_url}"
        }), 200
    except Exception as e:
        logger.error(f"Error setting webhook: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        # Инициализируем приложение Telegram, если еще не инициализировано
        if not telegram_app:
            init_telegram_app()
        
        # Обработка обновления через глобальное приложение
        update = Update.de_json(request.json, telegram_app.bot)
        asyncio.run(telegram_app.process_update(update))
        return '', 200
    except Exception as e:
        logger.error(f"Error processing update: {str(e)}")
        return jsonify({"status": "error"}), 500

async def main():
    # Проверка обязательных переменных
    required_vars = [
        ('TELEGRAM_TOKEN', TELEGRAM_TOKEN),
        ('OPENROUTER_API_KEY', OPENROUTER_API_KEY),
        ('WEBHOOK_URL', WEBHOOK_URL)
    ]
    
    missing = [name for name, value in required_vars if not value]
    
    if missing:
        logger.error(f"ОШИБКА: Отсутствуют обязательные переменные: {', '.join(missing)}")
        logger.error("Пожалуйста, установите их в настройках Render")
        exit(1)
    
    logger.info("="*50)
    logger.info(f"TELEGRAM_TOKEN: {'установлен' if TELEGRAM_TOKEN else 'отсутствует'}")
    logger.info(f"OPENROUTER_API_KEY: {'установлен' if OPENROUTER_API_KEY else 'отсутствует'}")
    logger.info(f"WEBHOOK_URL: {'установлен' if WEBHOOK_URL else 'отсутствует'}")
    logger.info(f"PORT: {PORT}")
    logger.info("="*50)
    
    # Инициализация приложения Telegram
    init_telegram_app()
    
    # Установка вебхука
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        await telegram_app.bot.set_webhook(webhook_url)
        logger.info(f"🚀 Вебхук установлен: {webhook_url}")
    else:
        logger.warning("⚠️ WEBHOOK_URL не установлен, вебхук не настроен")
    
    logger.info(f"🤖 Бот запущен и готов к работе!")

if __name__ == '__main__':
    # Создаем и запускаем цикл событий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Запускаем основную асинхронную задачу
        loop.run_until_complete(main())
        
        # Запускаем Flask в основном потоке
        app.run(host='0.0.0.0', port=PORT)
    except KeyboardInterrupt:
        logger.info("Приложение остановлено пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
    finally:
        loop.close()
