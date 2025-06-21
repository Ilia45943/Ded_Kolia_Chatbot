import os
import logging
import requests
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')
PORT = int(os.environ.get('PORT', 10000))
MODEL_NAME = "deepseek/deepseek-r1-0528-qwen3-8b:free"  # Проверенная рабочая модель

app = Flask(__name__)
telegram_app = None

class DedKolia:
    def __init__(self):
        self.default_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, ИИ сегодня не в духе... Как сам-то?",
            "Курва, технологии подводят! Ну расскажи, что у тебя нового?"
        ]

    def generate_response(self, user_message):
        """Умная генерация ответа через OpenRouter с полной обработкой ошибок"""
        try:
            if not OPENROUTER_API_KEY:
                raise ValueError("API ключ отсутствует")
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": WEBHOOK_URL or "https://ded-kolia-bot.com",
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

ded_kolia = DedKolia()

# Telegram обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = ded_kolia.generate_response(update.message.text)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

def init_telegram():
    global telegram_app
    if not telegram_app:
        telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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
        return jsonify({"status": "success", "url": webhook_url})
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
        response = ded_kolia.generate_response(test_message)
        return jsonify({
            "status": "success",
            "request": test_message,
            "response": response,
            "model": MODEL_NAME
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Проверка конфигурации
    required_vars = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        logger.error(f"Отсутствуют переменные: {', '.join(missing)}")
        exit(1)

    init_telegram()
    
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        telegram_app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=webhook_url
        )
    else:
        telegram_app.run_polling()
