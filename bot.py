import os
import logging
import requests
import json
import sys
import sqlite3
import re
import asyncio
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Явно устанавливаем кодировку UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Настройка логирования с UTF-8
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Проверка обязательных переменных
REQUIRED_VARS = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
for var in REQUIRED_VARS:
    if not os.getenv(var):
        logger.critical(f"Отсутствует обязательная переменная: {var}")
        exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
PORT = int(os.environ.get('PORT', 10000))

# Автоматическое определение URL на Render.com
RENDER_SERVICE_NAME = os.getenv('RENDER_SERVICE_NAME')
if RENDER_SERVICE_NAME:
    HOSTNAME = f"{RENDER_SERVICE_NAME}.onrender.com"
    logger.info(f"Определен Render Service Name: {RENDER_SERVICE_NAME}")
else:
    HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')
    logger.warning(f"RENDER_SERVICE_NAME не установлен, используем HOSTNAME: {HOSTNAME}")

# Логируем важные параметры
logger.info(f"Используемый HOSTNAME: {HOSTNAME}")
logger.info(f"TELEGRAM_TOKEN: {'установлен' if TELEGRAM_TOKEN else 'отсутствует'}")
logger.info(f"OPENROUTER_API_KEY: {'установлен' if OPENROUTER_API_KEY else 'отсутствует'}")

# Используем стабильную модель
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Flask(__name__)

# ====================== БАЗА ДАННЫХ ======================
def get_db_connection():
    conn = sqlite3.connect('ded_kolia.db')
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
        conn.commit()
    logger.info("База данных инициализирована")

# Инициализация БД при старте
init_db()

# ====================== ЛОГИКА БОТА ======================
class AIAssistant:
    def __init__(self):
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
                        conn.commit()
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
                conn.commit()
        except Exception as e:
            logger.error(f"Ошибка сохранения диалога: {str(e)}")

    def generate_response(self, user_id, message):
        try:
            # Сначала пробуем извлечь факты
            fact_response = self._extract_facts(user_id, message)
            if fact_response:
                return fact_response
            
            # Получаем историю диалога
            history = self.get_history(user_id)
            
            # Получаем известные факты о пользователе
            user_facts = []
            with get_db_connection() as conn:
                cursor = conn.execute("SELECT fact, value FROM user_facts WHERE user_id = ?", (user_id,))
                user_facts = cursor.fetchall()
            
            # Формируем контекст для нейросети
            facts = "\n".join(f"{fact['fact']}: {fact['value']}" for fact in user_facts) or "Ничего не известно"
            context = "\n".join(f"User: {msg['user_message']}\nBot: {msg['bot_response']}" for msg in history) if history else "Нет истории"
            
            # Системный промпт с персонажем
            system_prompt = f"""Ты — Дед Коля (67 лет). Отвечай как матерый старик:
            - Ругайся: курва, ебать в рот, блядь
            - Добавляй сарказм и чёрный юмор
            - Упоминай свой трактор и Опель Астру
            
            Факты о собеседнике:
            {facts}
            
            История диалога:
            {context}"""
            
            # Формируем запрос к нейросети
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json; charset=utf-8"
            }

            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                "temperature": 0.85,
                "max_tokens": 350
            }

            # Отправляем запрос
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=15
            )

            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                logger.error(f"Ошибка API: {response.status_code} - {response.text}")
                return self.default_responses[0]
                
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {str(e)}")
            return self.default_responses[1]

# Инициализация ИИ помощника
ai_assistant = AIAssistant()

# ====================== TELEGRAM ОБРАБОТЧИКИ ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")
        logger.info(f"Обработана команда /start от {update.effective_user.id}")
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
            conn.commit()
        await update.message.reply_text(f"✅ Окей, курва, запомнил: {fact_text}")
        logger.info(f"Пользователь {user_id} добавил факт: {fact_text}")
    except Exception as e:
        logger.error(f"Ошибка в команде /remember: {str(e)}")
        await update.message.reply_text("Блядь, не запомнилось... Давай ещё раз?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        user_input = update.message.text
        
        logger.info(f"Получено сообщение от {user_id}: {user_input}")
        
        response = ai_assistant.generate_response(user_id, user_input)
        ai_assistant.save_interaction(user_id, user_input, response)
        
        logger.info(f"Отправляем ответ: {response[:50]}...")
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Ой, курва, я сломался... Попробуй ещё раз!")

# ====================== ИНИЦИАЛИЗАЦИЯ TELEGRAM ======================
def create_telegram_app():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("remember", remember_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return application

telegram_app = create_telegram_app()

# ====================== FLASK РОУТЫ ======================
@app.route('/')
def home():
    return "🤖 Дед Коля в работе!"

@app.route('/test_ai')
def test_ai():
    try:
        test_user_id = "test_user"
        test_message = "Привет! Как дела?"
        logger.info(f"Тестовый запрос: {test_message}")
        response = ai_assistant.generate_response(test_user_id, test_message)
        
        return jsonify({
            "status": "success",
            "request": test_message,
            "response": response
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        logger.info("Получен вебхук от Telegram")
        update = Update.de_json(request.json, telegram_app.bot)
        telegram_app.update_queue.put(update)
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/check_env')
def check_env():
    """Проверка переменных окружения"""
    return jsonify({
        "HOSTNAME": HOSTNAME,
        "TELEGRAM_TOKEN": bool(TELEGRAM_TOKEN),
        "OPENROUTER_API_KEY": bool(OPENROUTER_API_KEY),
        "PORT": PORT,
        "MODEL": MODEL_NAME,
        "RENDER_SERVICE_NAME": RENDER_SERVICE_NAME
    })

async def set_webhook_task():
    """Устанавливаем вебхук"""
    webhook_url = f"https://{HOSTNAME}/telegram_webhook"
    logger.info(f"Устанавливаем вебхук на: {webhook_url}")
    await telegram_app.bot.set_webhook(webhook_url)
    logger.info(f"✅ Вебхук успешно установлен")

def set_webhook():
    """Синхронная обертка для установки вебхука"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(set_webhook_task())
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {str(e)}")

def run_bot():
    """Запускаем бота в фоновом режиме"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(telegram_app.start())
        logger.info("🤖 Бот запущен и готов к работе!")
        loop.run_forever()
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {str(e)}")

if __name__ == '__main__':
    # Устанавливаем вебхук
    set_webhook()
    
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Запускаем Flask
    logger.info(f"🌐 Запускаем сервер на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)