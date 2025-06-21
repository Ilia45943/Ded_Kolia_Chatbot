import os
import logging
import requests
import sqlite3
import re
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
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

if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
    logger.critical("Не установлены TELEGRAM_TOKEN или OPENROUTER_API_KEY")
    exit(1)

MODEL_NAME = "mistralai/mistral-7b-instruct:free"

# Упрощенная инициализация базы данных
def init_db():
    with sqlite3.connect('bot.db') as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_data (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                city TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_message TEXT,
                bot_response TEXT
            )
        ''')

init_db()

# Функции работы с БД
def save_history(user_id, user_message, bot_response):
    try:
        with sqlite3.connect('bot.db') as conn:
            conn.execute('''
                INSERT INTO history (user_id, user_message, bot_response)
                VALUES (?, ?, ?)
            ''', (user_id, user_message, bot_response))
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {str(e)}")

def get_history(user_id, limit=3):
    try:
        with sqlite3.connect('bot.db') as conn:
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

def get_user_data(user_id):
    try:
        with sqlite3.connect('bot.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT name, city FROM user_data WHERE user_id = ?', (user_id,))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка получения данных пользователя: {str(e)}")
        return None, None

def update_user_data(user_id, name=None, city=None):
    try:
        with sqlite3.connect('bot.db') as conn:
            # Проверяем существование записи
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM user_data WHERE user_id = ?', (user_id,))
            exists = cursor.fetchone()
            
            if exists:
                if name:
                    conn.execute('UPDATE user_data SET name = ? WHERE user_id = ?', (name, user_id))
                if city:
                    conn.execute('UPDATE user_data SET city = ? WHERE user_id = ?', (city, user_id))
            else:
                conn.execute('INSERT INTO user_data (user_id, name, city) VALUES (?, ?, ?)', 
                             (user_id, name or '', city or ''))
    except Exception as e:
        logger.error(f"Ошибка обновления данных пользователя: {str(e)}")

# Упрощенная генерация ответа
def generate_response(user_id, user_message):
    try:
        # Проверка на команды управления данными
        name_match = re.search(r"(меня зовут|мое имя|зовут меня)\s+([а-яА-ЯёЁ\s]+)", user_message, re.IGNORECASE)
        city_match = re.search(r"(я из|живу в|город)\s+([а-яА-ЯёЁ\s-]+)", user_message, re.IGNORECASE)
        
        if name_match:
            name = name_match.group(2).strip()
            update_user_data(user_id, name=name)
            return f"Окей, {name}, запомнил твое имя!"
        
        if city_match:
            city = city_match.group(2).strip()
            update_user_data(user_id, city=city)
            return f"Так ты из {city}? Заметил!"
        
        # Упрощенный контекст для нейросети
        name, city = get_user_data(user_id)
        context = f"Ты - Дед Коля (67 лет). Отвечай грубо, с матом и сарказмом. Ругайся на жизнь, вспоминай трактор и Опель Астру."
        
        if name or city:
            context += "\n\nО собеседнике:"
            if name:
                context += f"\n- Зовут: {name}"
            if city:
                context += f"\n- Город: {city}"
        
        # Упрощенный запрос к нейросети
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
            "temperature": 0.7,
            "max_tokens": 150
        }
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=10  # Уменьшенный таймаут
        )
        
        return response.json()['choices'][0]['message']['content'] if response.status_code == 200 else "Черт, нейросеть глючит... Давай позже!"
            
    except Exception as e:
        logger.error(f"Ошибка генерации: {str(e)}")
        return "Блядь, я сломался... Попробуй еще разок!"

# Обработчики команд
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_message = update.message.text
    logger.info(f"Получено сообщение от {user_id}: {user_message}")
    
    response = generate_response(user_id, user_message)
    logger.info(f"Сгенерирован ответ: {response}")
    
    save_history(user_id, user_message, response)
    await update.message.reply_text(response)

# Настройка и запуск бота
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот запущен в режиме Long Polling...")
    application.run_polling()

if __name__ == '__main__':
    main()