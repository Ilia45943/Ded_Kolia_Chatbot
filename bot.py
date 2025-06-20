import os
import logging
import sqlite3
import requests
import re
from datetime import datetime
from telegram import Update, Bot
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
TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
PORT = 10000  # Явно указываем порт 10000
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
MODEL_NAME = "DeepSeek R1 0528 Qwen 3.8B"

# Инициализация Flask
flask_app = Flask(__name__)

# ====================== БАЗА ЗНАНИЙ ======================
class KnowledgeBase:
    def __init__(self, db_path="/tmp/knowledge.db"):
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
            conn.commit()
    
    def add_user_fact(self, user_id, fact, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO user_facts (user_id, fact, value, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, fact, value, datetime.now().isoformat()))
            conn.commit()
    
    def get_user_facts(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT fact, value FROM user_facts WHERE user_id = ?
            """, (user_id,))
            return cursor.fetchall()

# ====================== ПАМЯТЬ ДИАЛОГОВ ======================
class Memory:
    def __init__(self, db_path="/tmp/sessions.db"):
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
            conn.commit()
    
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
            conn.commit()

# ====================== ЛОГИКА ДЕДА КОЛИ ======================
class DedKolia:
    def __init__(self, knowledge_base, memory):
        self.kb = knowledge_base
        self.memory = memory
        self.model = "deepseek/deepseek-r1:free"
    
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
            "model": self.model,
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

# Инициализация
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

# Flask роуты
@flask_app.route('/')
def home():
    return f"🤖 Дед Коля в работе! Модель: {MODEL_NAME}"

@flask_app.route('/set_webhook', methods=['GET'])
async def set_webhook():
    try:
        bot = Bot(TOKEN)
        url = f"{WEBHOOK_URL}/telegram_webhook"
        await bot.set_webhook(url)
        return jsonify({"status": "success", "message": f"Webhook set to {url}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@flask_app.route('/telegram_webhook', methods=['POST'])
async def telegram_webhook():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    try:
        await application.initialize()
        update = Update.de_json(request.json, application.bot)
        await application.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Error processing update: {str(e)}")
        return jsonify({"status": "error"}), 500

if __name__ == '__main__':
    # Детальная диагностика переменных
    logger.info("="*50)
    logger.info("Проверка переменных окружения:")
    logger.info(f"TELEGRAM_TOKEN: {'УСТАНОВЛЕН' if TOKEN else 'ОТСУТСТВУЕТ'}")
    logger.info(f"OPENROUTER_API_KEY: {'УСТАНОВЛЕН' if OPENROUTER_API_KEY else 'ОТСУТСТВУЕТ'}")
    logger.info(f"WEBHOOK_URL: {'УСТАНОВЛЕН' if WEBHOOK_URL else 'ОТСУТСТВУЕТ'}")
    logger.info(f"PORT: {PORT} (фиксированный)")
    logger.info("="*50)
    
    if not TOKEN or not OPENROUTER_API_KEY or not WEBHOOK_URL:
        logger.error("ОШИБКА: Отсутствуют обязательные переменные окружения!")
        logger.error("Убедитесь, что в настройках Render добавлены:")
        logger.error("1. TELEGRAM_TOKEN")
        logger.error("2. OPENROUTER_API_KEY")
        logger.error("3. WEBHOOK_URL")
        exit(1)
    
    logger.info(f"🚀 Запуск бота на порту {PORT} с моделью: {MODEL_NAME}...")
    flask_app.run(host='0.0.0.0', port=PORT)
