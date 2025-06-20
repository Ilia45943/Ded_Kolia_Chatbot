import os
import logging
import sqlite3
import requests
import re
import random
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ContextTypes,
    TypeHandler
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
AI21_API_KEY = os.getenv('AI21_API_KEY')
PORT = int(os.environ.get('PORT', 5000))
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # Полный URL вашего приложения на Render

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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS general_knowledge (
                    id INTEGER PRIMARY KEY,
                    topic TEXT NOT NULL,
                    fact TEXT NOT NULL,
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
                SELECT fact, value 
                FROM user_facts 
                WHERE user_id = ? 
                ORDER BY timestamp DESC
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
                SELECT user_message, bot_response 
                FROM sessions 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (user_id, limit))
            return cursor.fetchall()
    
    def save_interaction(self, user_id, user_message, bot_response):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO sessions 
                (user_id, timestamp, user_message, bot_response) 
                VALUES (?, ?, ?, ?)
            """, (
                user_id,
                datetime.now().isoformat(),
                user_message,
                bot_response
            ))
            conn.commit()

# ====================== ЛОГИКА ДЕДА КОЛИ ======================
class DedKolia:
    def __init__(self, knowledge_base, memory):
        self.kb = knowledge_base
        self.memory = memory
        self.base_prompt = """
        Ты — Дед Коля (67 лет). Характер:
        - Матерый, саркастичный старик
        - Чёрный юмор, матерный язык
        - Любит: бухло, трактор, Люду, Федю
        - Фразы: "курва", "ебать в рот", "пьянь ходячая", "чек, коровы идут"
        - История: собрал трактор, ездит на Опеле Астре 96-го года
        
        Известное о собеседнике:
        {user_facts}
        
        Контекст диалога:
        {context}
        
        Текущее сообщение:
        User: {message}
        Дед Коля:
        """
    
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
        # Пытаемся извлечь факты
        fact_response = self._extract_facts(user_id, message)
        if fact_response:
            return fact_response
        
        # Получаем историю диалога
        history = self.memory.get_history(user_id)
        context_lines = []
        for user_msg, bot_msg in history:
            context_lines.append(f"User: {user_msg}")
            context_lines.append(f"Дед Коля: {bot_msg}")
        context = "\n".join(context_lines) if context_lines else "Нет истории"
        
        # Получаем известные факты о пользователе
        user_facts = self.kb.get_user_facts(user_id)
        facts_str = "\n".join([f"{fact[0]}: {fact[1]}" for fact in user_facts]) if user_facts else "Ничего не известно"
        
        # Формируем промпт
        prompt = self.base_prompt.format(
            user_facts=facts_str,
            context=context,
            message=message
        )
        
        # Запрос к AI21
        try:
            response = requests.post(
                "https://api.ai21.com/studio/v1/jamba-instruct/complete",
                headers={"Authorization": f"Bearer {AI21_API_KEY}"},
                json={
                    "model": "jamba-1.5",
                    "prompt": prompt,
                    "temperature": 0.85,
                    "maxTokens": 250,
                    "stopSequences": ["\nUser:"]
                },
                timeout=15
            )
            
            if response.status_code == 200:
                return response.json()['completions'][0]['data']['text']
            else:
                logger.error(f"AI21 API error: {response.status_code}")
                return "Ой, курва, что-то сломалось... Давай позже!"
        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            return "Чёрт, сломалось! Попробуй ещё раз."

# ====================== ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ======================
knowledge_base = KnowledgeBase()
memory = Memory()
ded_kolia = DedKolia(knowledge_base, memory)

# ====================== ТЕЛЕГРАМ ОБРАБОТЧИКИ ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Дед Коля на связи! Шо надо, пьянь ходячая?")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if not context.args:
        await update.message.reply_text("Чё запоминать-то? Используй: /remember я люблю пиво")
        return
    
    fact_text = " ".join(context.args)
    knowledge_base.add_user_fact(user_id, "факт", fact_text)
    await update.message.reply_text(f"Окей, курва, запомнил: {fact_text}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_input = update.message.text
    
    # Генерируем ответ
    response = ded_kolia.generate_response(user_id, user_input)
    
    # Сохраняем в историю
    memory.save_interaction(user_id, user_input, response)
    
    # Отправляем ответ
    await update.message.reply_text(response)

# ====================== FLASK РОУТЫ ДЛЯ WEBHOOK ======================
@flask_app.route('/')
def home():
    return "Дед Коля в работе! Бот запущен и готов к общению."

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
    # Создаем приложение Telegram
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обрабатываем входящее обновление
    try:
        await application.initialize()
        update = Update.de_json(request.json, application.bot)
        await application.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Error processing update: {str(e)}")
        return jsonify({"status": "error"}), 500

# ====================== ЗАПУСК ПРИЛОЖЕНИЯ ======================
if __name__ == '__main__':
    # Проверка обязательных переменных
    if not TOKEN or not AI21_API_KEY or not WEBHOOK_URL:
        logger.error("Missing required environment variables!")
        exit(1)
    
    logger.info("Starting Ded Kolia Telegram bot...")
    logger.info(f"Webhook URL: {WEBHOOK_URL}/telegram_webhook")
    
    # Запуск Flask приложения
    flask_app.run(host='0.0.0.0', port=PORT)
