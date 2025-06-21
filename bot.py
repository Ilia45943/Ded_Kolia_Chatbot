import os
import logging
import requests
import json
import asyncio
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    Updater,
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

app = Flask(__name__)

class AIAssistant:
    def __init__(self):
        self.default_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    async def generate_response_async(self, user_message):
        """Асинхронная генерация ответа через OpenRouter"""
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

            # Используем асинхронные HTTP-запросы
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

ai_assistant = AIAssistant()

# Telegram обработчики
async def start(update: Update, context: CallbackContext):
    logger.info(f"Обработка команды /start от пользователя {update.effective_user.id}")
    await update.message.reply_text("👴 Дед Коля на связи! Шо надо, курва?")

async def handle_message(update: Update, context: CallbackContext):
    try:
        logger.info(f"Получено сообщение: {update.message.text}")
        # Используем асинхронную версию генерации ответа
        response = await ai_assistant.generate_response_async(update.message.text)
        logger.info(f"Отправка ответа: {response}")
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

# Создаем приложение Telegram
def create_application():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return application

telegram_app = create_application()

# Flask роуты
@app.route('/')
def home():
    return "🤖 Дед Коля в работе!"

@app.route('/test_ai')
def test_ai():
    try:
        test_message = "Привет! Как дела?"
        # Для тестового роута используем синхронный вызов
        response = asyncio.run(ai_assistant.generate_response_async(test_message))
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
    """СИНХРОННЫЙ обработчик вебхука"""
    try:
        logger.info("Получено обновление от Telegram")
        # Обрабатываем обновление в отдельном потоке
        update = Update.de_json(request.json, telegram_app.bot)
        
        # Запускаем асинхронную обработку в отдельном потоке
        asyncio.run_coroutine_threadsafe(
            telegram_app.process_update(update), 
            telegram_app.updater.bot.loop
        )
        
        return '', 200
    except Exception as e:
        logger.error(f"Ошибка вебхука: {str(e)}")
        return jsonify({"status": "error"}), 500

async def run_bot():
    """Запускаем бота и устанавливаем вебхук"""
    try:
        # Устанавливаем вебхук
        webhook_url = f"https://{HOSTNAME}/telegram_webhook"
        await telegram_app.bot.set_webhook(webhook_url)
        logger.info(f"Вебхук установлен: {webhook_url}")
        
        # Запускаем обработку обновлений
        logger.info("🤖 Бот запущен и готов к работе!")
    except Exception as e:
        logger.critical(f"Ошибка запуска бота: {str(e)}")

def start_bot():
    """Запускаем бота в отдельном потоке"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())
    loop.run_forever()  # Важно: держим петлю активной

if __name__ == '__main__':
    # Запускаем бота в отдельном потоке
    import threading
    
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Запускаем Flask с поддержкой async
    logger.info(f"🌐 Запускаем сервер на порту {PORT}")
    
    # Для локальной разработки
    if os.getenv('ENV') == 'development':
        app.run(host='0.0.0.0', port=PORT, use_reloader=False)
    else:
        # Для продакшена используем ASGI-сервер
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        
        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        asyncio.run(serve(app, config))
