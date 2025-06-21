import os
import logging
import requests
import json
import sys
import asyncio

from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Настройка кодировки UTF-8
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
REQUIRED_VARS = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
for var in REQUIRED_VARS:
    if not os.getenv(var):
        logger.critical(f"❌ Переменная {var} не задана!")
        exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
PORT = int(os.environ.get('PORT', 10000))
HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Flask(__name__)

# Класс ИИ помощника
class AIAssistant:
    def __init__(self):
        self.default_responses = [
            "Сервер опять дурит... Чё хотел, курва?",
            "Да блядь, нейросеть в отпуске. А ты как там?",
            "Ёбушки-воробушки, технологии подбухнули. Ну чё расскажешь?"
        ]

    def generate_response(self, user_message):
        try:
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты — Дед Коля, деревенский дед 67 лет. "
                            "Грубиян, материшься, с чёрным юмором, вспоминаешь трактор, Опель Астру 96-го года, "
                            "жену бабу Люду, курей и свиней. Пьёшь с другом Федей. Говоришь на простом деревенском языке."
                        )
                    },
                    {"role": "user", "content": user_message}
                ],
                "temperature": 0.85,
                "max_tokens": 300
            }

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=10
            )

            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
            else:
                logger.error(f"❌ OpenRouter API: {response.status_code} — {response.text}")
                return self.default_responses[0]

        except Exception as e:
            logger.error(f"❌ Ошибка ответа ИИ: {str(e)}")
            return self.default_responses[1]

ai_assistant = AIAssistant()

# Telegram-хендлеры
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_message = update.message.text
        response = ai_assistant.generate_response(user_message)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"❌ Ошибка обработки Telegram-сообщения: {str(e)}")
        await update.message.reply_text("Блядь, чёт сломалось... Попробуй позже.")

# Telegram-приложение
def create_application():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application

telegram_app = create_application()

# Flask маршруты
@app.route('/')
def home():
    return "✅ Дед Коля запущен!"

@app.route('/test_ai')
def test_ai():
    try:
        test_input = "Ну как ты, Дед Коля?"
        result = ai_assistant.generate_response(test_input)
        return jsonify({"status": "success", "input": test_input, "response": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/telegram_webhook', methods=['POST'])
async def telegram_webhook():
    try:
        update = Update.de_json(request.json, telegram_app.bot)
        await telegram_app.update_queue.put(update)
        return '', 200
    except Exception as e:
        logger.error(f"❌ Ошибка Webhook: {str(e)}")
        return jsonify({"status": "error"}), 500

# Автоустановка Webhook
async def run_bot():
    try:
        webhook_url = f"https://{HOSTNAME}/telegram_webhook"
        await telegram_app.bot.set_webhook(webhook_url)
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        await telegram_app.updater.idle()
    except Exception as e:
        logger.critical(f"❌ Ошибка запуска бота: {str(e)}")

@app.before_first_request
def activate_bot():
    logger.info("🚀 Запускаем Деда Колю...")
    asyncio.run(run_bot())

if __name__ == '__main__':
    logger.info(f"🌍 Flask запускается на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)
