import os
import logging
import aiohttp
import asyncio
from quart import Quart, request, jsonify
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
REQUIRED_VARS = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
for var in REQUIRED_VARS:
    if not os.getenv(var):
        logger.critical(f"Отсутствует обязательная переменная: {var}")
        exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
PORT = int(os.environ.get('PORT', 10000))
HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')

# Используем стабильную модель
MODEL_NAME = "mistralai/mistral-7b-instruct:free"

app = Quart(__name__)

class AIAssistant:
    def __init__(self):
        self.default_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    async def generate_response(self, user_message):
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

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=10
                ) as response:
                    
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    else:
                        error_text = await response.text()
                        logger.error(f"Ошибка API: {response.status} - {error_text}")
                        return self.default_responses[0]
                
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {str(e)}")
            return self.default_responses[1]

# Telegram обработчики
async def start(update: Update, context: CallbackContext):
    logger.info(f"Обработка команды /start от пользователя {update.effective_user.id}")
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: CallbackContext):
    try:
        logger.info(f"Получено сообщение: {update.message.text}")
        ai_assistant = app.config.get('ai_assistant')
        response = await ai_assistant.generate_response(update.message.text)
        logger.info(f"Отправка ответа: {response}")
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

# Создаем и настраиваем приложение Telegram
def setup_telegram():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return application

# Quart роуты
@app.route('/')
async def home():
    return "🤖 Дед Коля в работе!"

@app.route('/test_ai')
async def test_ai():
    try:
        test_message = "Привет! Как дела?"
        ai_assistant = app.config.get('ai_assistant')
        response = await ai_assistant.generate_response(test_message)
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
async def telegram_webhook():
    try:
        logger.info("Получено обновление от Telegram")
        application = app.config.get('telegram_app')
        
        if not application:
            logger.error("Telegram Application не инициализирован!")
            return jsonify({"status": "error"}), 500
            
        data = await request.get_json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return jsonify({"status": "error"}), 500

async def run_bot():
    """Запускаем бота и устанавливаем вебхук"""
    try:
        # Инициализируем приложение Telegram
        telegram_app = setup_telegram()
        app.config['telegram_app'] = telegram_app
        
        # Инициализируем AI Assistant
        ai_assistant = AIAssistant()
        app.config['ai_assistant'] = ai_assistant
        
        # Устанавливаем вебхук
        webhook_url = f"https://{HOSTNAME}/telegram_webhook"
        await telegram_app.bot.set_webhook(webhook_url)
        logger.info(f"🚀 Вебхук установлен: {webhook_url}")
        
        logger.info("🤖 ИИ Дед Коля инициализирован и готов к работе!")
        return telegram_app
    except Exception as e:
        logger.critical(f"Ошибка запуска бота: {str(e)}")
        raise

async def main():
    """Основная асинхронная функция для запуска всего приложения"""
    # Запускаем бота
    await run_bot()
    
    # Запускаем сервер
    logger.info(f"🌐 Запускаем сервер на порту {PORT}")
    await app.run_task(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    asyncio.run(main())
