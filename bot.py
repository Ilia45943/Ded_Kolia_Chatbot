import os
import logging
import requests
import asyncio
from threading import Thread
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка обязательных переменных
REQUIRED_VARS = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
for var in REQUIRED_VARS:
    if not os.getenv(var):
        logger.critical(f"Отсутствует обязательная переменная: {var}")
        exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')
PORT = int(os.environ.get('PORT', 10000))

# Используем стабильную модель
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Flask(__name__)
telegram_app = None

class AIAssistant:
    def __init__(self):
        self.default_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    def generate_response(self, user_message):
        """Простой и надежный способ генерации ответа"""
        try:
            if not OPENROUTER_API_KEY:
                raise ValueError("API ключ отсутствует")
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": WEBHOOK_URL or "https://ai-bot.com",
                "X-Title": "Дед Коля Бот"
            }

            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {
                        "role": "system", 
                        "content": "Ты - Дед Коля (67 лет). Отвечай грубо, с матом и сарказмом. Ругайся на жизнь, вспоминай трактор и Опель Астру."
                    },
                    {
                        "role": "user",
                        "content": user_message
                    }
                ],
                "temperature": 0.8,
                "max_tokens": 300
            }

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                logger.error(f"Ошибка API: {response.status_code} - {response.text}")
                return self.default_responses[0]
                
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {str(e)}")
            return self.default_responses[1]

ai_assistant = AIAssistant()

# Telegram обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = ai_assistant.generate_response(update.message.text)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

def init_telegram():
    """Инициализация Telegram приложения"""
    global telegram_app
    if not telegram_app:
        try:
            telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
            telegram_app.add_handler(CommandHandler("start", start))
            telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            logger.info("Telegram бот инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации Telegram: {str(e)}")
            raise

# Flask роуты
@app.route('/')
def home():
    return "🤖 Дед Коля в работе!"

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    try:
        init_telegram()
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        telegram_app.bot.set_webhook(webhook_url)
        return jsonify({
            "status": "success",
            "url": webhook_url
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        init_telegram()
        update = Update.de_json(request.json, telegram_app.bot)
        telegram_app.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

@app.route('/test_ai')
def test_ai():
    try:
        test_message = "Привет! Как дела?"
        response = ai_assistant.generate_response(test_message)
        return jsonify({
            "status": "success",
            "request": test_message,
            "response": response
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def run_flask():
    """Запуск Flask в основном потоке"""
    app.run(host='0.0.0.0', port=PORT)

def run_telegram():
    """Запуск Telegram бота в отдельном потоке"""
    init_telegram()
    
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        telegram_app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=webhook_url,
            drop_pending_updates=True
        )
        logger.info(f"🚀 Бот запущен в webhook режиме: {webhook_url}")
    else:
        telegram_app.run_polling(drop_pending_updates=True)
        logger.info("🤖 Бот запущен в polling режиме")

if __name__ == '__main__':
    logger.info("="*50)
    logger.info(f"Запуск бота (WEBHOOK: {WEBHOOK_URL or 'POLLING'})")
    logger.info(f"Используемая модель: {MODEL_NAME}")
    logger.info("="*50)

    # Запускаем Telegram бота в отдельном потоке
    telegram_thread = Thread(target=run_telegram)
    telegram_thread.daemon = True
    telegram_thread.start()

    # Запускаем Flask в основном потоке
    try:
        run_flask()
    except Exception as e:
        logger.critical(f"Критическая ошибка Flask: {str(e)}")
