import os
import logging
import requests
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# 1. Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Конфигурация (проверяем переменные)
REQUIRED_VARS = ['TELEGRAM_TOKEN', 'OPENROUTER_API_KEY']
for var in REQUIRED_VARS:
    if not os.getenv(var):
        logger.critical(f"Отсутствует обязательная переменная: {var}")
        exit(1)

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')
PORT = int(os.environ.get('PORT', 10000))

# 3. Доступные модели (с автоматическим переключением)
MODELS = [
    "mistralai/mistral-7b-instruct:free",  # Наиболее стабильная
    "deepseek/deepseek-r1-0528-qwen3-8b:free",
    "openchat/openchat-7b:free"
]

app = Flask(__name__)
telegram_app = None

class AIAssistant:
    def __init__(self):
        self.current_model = MODELS[0]
        self.fallback_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    async def generate_response(self, user_message: str) -> str:
        """Асинхронная генерация ответа с автоматическим переключением моделей"""
        for model in MODELS:
            try:
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": WEBHOOK_URL or "https://ai-bot.com",
                    "X-Title": "Дед Коля Бот"
                }

                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты - Дед Коля (67 лет). Отвечай грубо, с матом и сарказмом."
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
                        timeout=15
                    ) as response:
                        
                        if response.status == 200:
                            data = await response.json()
                            self.current_model = model
                            return data['choices'][0]['message']['content']
                        
                        logger.warning(f"Модель {model} не сработала: {response.status}")
                        
            except Exception as e:
                logger.warning(f"Ошибка запроса к модели {model}: {str(e)}")
                continue

        return self.fallback_responses[hash(user_message) % len(self.fallback_responses)]

ai = AIAssistant()

# 4. Telegram обработчики (асинхронные)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👴 Дед Коля на связи! Используем модель: {ai.current_model}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = await ai.generate_response(update.message.text)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

# 5. Инициализация Telegram (с правильным shutdown)
async def init_telegram():
    global telegram_app
    if not telegram_app:
        telegram_app = (
            Application.builder()
            .token(TELEGRAM_TOKEN)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

async def post_init(app: Application):
    logger.info("Бот успешно инициализирован")
    if WEBHOOK_URL:
        await app.bot.set_webhook(f"{WEBHOOK_URL}/telegram_webhook")

async def post_shutdown(app: Application):
    logger.info("Бот корректно завершает работу")
    await app.bot.delete_webhook()

# 6. Flask роуты (синхронные)
@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "model": ai.current_model,
        "webhook": bool(WEBHOOK_URL)
    })

@app.route('/telegram_webhook', methods=['POST'])
async def telegram_webhook():
    try:
        await init_telegram()
        update = Update.de_json(await request.json, telegram_app.bot)
        await telegram_app.process_update(update)
        return '', 200
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500

# 7. Запуск приложения
def run_app():
    import asyncio
    from threading import Thread

    # Запускаем Flask в отдельном потоке
    def run_flask():
        app.run(host='0.0.0.0', port=PORT)

    Thread(target=run_flask).start()

    # Запускаем Telegram бота
    async def run_telegram():
        await init_telegram()
        if WEBHOOK_URL:
            await telegram_app.start()
            await telegram_app.updater.start_polling()  # Для обработки сообщений
        else:
            await telegram_app.run_polling()

    asyncio.run(run_telegram())

if __name__ == '__main__':
    logger.info("="*50)
    logger.info(f"Запуск бота (WEBHOOK: {WEBHOOK_URL or 'POLLING'})")
    logger.info(f"Доступные модели: {MODELS}")
    logger.info("="*50)

    try:
        run_app()
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"Критическая ошибка: {str(e)}")
    finally:
        if telegram_app:
            asyncio.run(telegram_app.shutdown())
