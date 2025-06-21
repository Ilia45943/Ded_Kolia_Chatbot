import os
import logging
import requests
import json
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    Dispatcher
)

# Явно устанавливаем кодировку UTF-8
import sys
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

# Используем стабильную модель
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Flask(__name__)

# Создаем бота Telegram
bot = Bot(token=TELEGRAM_TOKEN)

class AIAssistant:
    def __init__(self):
        self.default_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    def generate_response(self, user_message):
        """Генерация ответа с обработкой кодировки"""
        try:
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json; charset=utf-8"
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

            # Используем явное указание кодировки
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=10
            )

            if response.status_code == 200:
                # Обработка ответа с явным указанием кодировки
                content = response.json()['choices'][0]['message']['content']
                return content.encode('utf-8').decode('utf-8')
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

# Инициализация диспетчера
def setup_dispatcher():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Инициализируем диспетчер
    dispatcher = Dispatcher(application.bot, None, application=application)
    return dispatcher, application

# Создаем диспетчер и приложение
dispatcher, application = setup_dispatcher()

# Flask роуты
@app.route('/')
def home():
    return "🤖 Дед Коля в работе!"

@app.route('/test_ai')
def test_ai():
    try:
        test_message = "Привет! Как дела?"
        response = ai_assistant.generate_response(test_message)
        
        # Формируем ответ с явным указанием кодировки
        resp = jsonify({
            "status": "success",
            "request": test_message,
            "response": response
        })
        resp.headers['Content-Type'] = 'application/json; charset=utf-8'
        return resp
    except Exception as e:
        error_resp = jsonify({
            "status": "error",
            "message": str(e)
        })
        error_resp.headers['Content-Type'] = 'application/json; charset=utf-8'
        return error_resp, 500

@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    try:
        # Обрабатываем входящее обновление
        update = Update.de_json(request.json, bot)
        dispatcher.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

def set_webhook():
    """Устанавливаем вебхук"""
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/telegram_webhook"
    bot.set_webhook(webhook_url)
    logger.info(f"Вебхук установлен: {webhook_url}")

def start_bot():
    """Запускаем бота в отдельном потоке"""
    try:
        # Устанавливаем вебхук
        set_webhook()
        
        # Запускаем обработку обновлений
        application.run_polling()
        logger.info("🤖 Бот запущен и готов к работе!")
    except Exception as e:
        logger.critical(f"Ошибка запуска бота: {str(e)}")

if __name__ == '__main__':
    # Запускаем бота в отдельном потоке
    import threading
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Запускаем Flask
    logger.info(f"🌐 Запускаем Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT)
