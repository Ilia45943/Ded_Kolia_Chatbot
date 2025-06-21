import os
import logging
import requests
import sqlite3
import re
from datetime import datetime
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка обязательных переменных
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
PORT = int(os.environ.get('PORT', 10000))
HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.critical("Не установлены TELEGRAM_TOKEN или OPENROUTER_API_KEY")
    exit(1)

# Используем стабильную модель
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Flask(__name__)

# Инициализация приложения Telegram
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_data (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            city TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_message TEXT,
            bot_response TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Сохраняем сообщение в историю
def save_history(user_id, user_message, bot_response):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO history (user_id, user_message, bot_response)
            VALUES (?, ?, ?)
        ''', (user_id, user_message, bot_response))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {str(e)}")

# Получаем историю сообщений
def get_history(user_id, limit=3):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_message, bot_response 
            FROM history 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit))
        return cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения истории: {str(e)}")
        return []

# Получаем данные пользователя
def get_user_data(user_id):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name, city FROM user_data WHERE user_id = ?', (user_id,))
        return cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка получения данных пользователя: {str(e)}")
        return None

# Обновляем данные пользователя
def update_user_data(user_id, name=None, city=None):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        
        # Проверяем, есть ли уже запись
        cursor.execute('SELECT 1 FROM user_data WHERE user_id = ?', (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            if name:
                cursor.execute('UPDATE user_data SET name = ? WHERE user_id = ?', (name, user_id))
            if city:
                cursor.execute('UPDATE user_data SET city = ? WHERE user_id = ?', (city, user_id))
        else:
            cursor.execute('INSERT INTO user_data (user_id, name, city) VALUES (?, ?, ?)', 
                          (user_id, name or '', city or ''))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка обновления данных пользователя: {str(e)}")

# Генерация ответа с помощью нейросети
def generate_response(user_id, user_message):
    try:
        # Пробуем извлечь данные из сообщения
        name_match = re.search(r"(меня зовут|мое имя|зовут меня) ([а-яА-ЯёЁ]+)", user_message, re.IGNORECASE)
        city_match = re.search(r"(я из|живу в|город) ([а-яА-ЯёЁ\s]+)", user_message, re.IGNORECASE)
        
        if name_match:
            name = name_match.group(2).strip()
            update_user_data(user_id, name=name)
            return f"Окей, запомнил что твоё имя - {name}!"
        
        if city_match:
            city = city_match.group(2).strip()
            update_user_data(user_id, city=city)
            return f"Окей, запомнил что твой город - {city}!"
        
        # Получаем данные пользователя и историю
        user_data = get_user_data(user_id) or (None, None)
        history = get_history(user_id)
        
        # Формируем контекст для нейросети
        context = "Ты - Дед Коля (67 лет). Отвечай грубо, с матом и сарказмом. Ругайся на жизнь, вспоминай трактор и Опель Астру.\n\n"
        
        if user_data[0] or user_data[1]:
            context += "Я знаю о тебе:\n"
            if user_data[0]:
                context += f"- Имя: {user_data[0]}\n"
            if user_data[1]:
                context += f"- Город: {user_data[1]}\n"
            context += "\n"
        
        if history:
            context += "История разговора:\n"
            for user_msg, bot_resp in history:
                context += f"Ты: {user_msg}\n"
                context += f"Я: {bot_resp}\n"
            context += "\n"
        
        context += f"Сейчас ты сказал: {user_message}"
        
        # Запрос к нейросети
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.8,
            "max_tokens": 300
        }
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=15
        )
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            logger.error(f"Ошибка API: {response.status_code} - {response.text}")
            return "Чёрт, нейросеть глючит... Давай просто поболтаем?"
            
    except Exception as e:
        logger.error(f"Ошибка генерации ответа: {str(e)}")
        return "Блядь, я сломался... Попробуй ещё раз!"

# Обработчики команд
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # Генерируем ответ
    response = generate_response(user_id, user_message)
    
    # Сохраняем в историю
    save_history(user_id, user_message, response)
    
    # Отправляем ответ
    await update.message.reply_text(response)

# Регистрируем обработчики
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Обработчик вебхука
@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        update = Update.de_json(request.json, application.bot)
        application.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return 'Error', 500

# Установка вебхука
def set_webhook():
    webhook_url = f"https://{HOSTNAME}/telegram_webhook"
    application.bot.set_webhook(webhook_url)
    logger.info(f"Вебхук установлен: {webhook_url}")

# Главная страница для проверки
@app.route('/')
def home():
    return "🤖 Дед Коля в работе!"

# Запуск приложения
if __name__ == '__main__':
    set_webhook()
    app.run(host='0.0.0.0', port=PORT, debug=False)