import os
import logging
import requests
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация (обязательные параметры)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '').rstrip('/')
PORT = int(os.environ.get('PORT', 10000))

# Проверенные модели (если одна не работает, пробуем другую)
MODELS = [
    "deepseek/deepseek-r1-0528-qwen3-8b:free",
    "mistralai/mistral-7b-instruct:free",
    "openchat/openchat-7b:free"
]

app = Flask(__name__)
telegram_app = None

class AIAssistant:
    def __init__(self):
        self.current_model = MODELS[0]  # Начинаем с первой модели
        self.fallback_responses = [
            "Чёрт, нейросеть глючит... Давай просто поболтаем?",
            "Эх, технологии сегодня не в духе... Как сам-то?",
            "Курва, сервера тупят! Ну расскажи, что у тебя нового?"
        ]

    def generate_response(self, user_message: str) -> str:
        """Генерация ответа через OpenRouter с автоматическим переключением моделей"""
        last_error = None
        
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
                            "content": "Ты - Дед Коля (67 лет). Отвечай грубо, с матом и сарказмом. "
                                      "Ругайся на жизнь, вспоминай трактор и Опель Астру."
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
                    timeout=15  # Увеличенный таймаут
                )

                if response.status_code == 200:
                    self.current_model = model  # Запоминаем работающую модель
                    return response.json()['choices'][0]['message']['content']
                
                last_error = f"Status {response.status_code}: {response.text}"
                logger.warning(f"Модель {model} не сработала: {last_error}")

            except requests.exceptions.RequestException as e:
                last_error = str(e)
                logger.warning(f"Ошибка запроса к модели {model}: {last_error}")
                continue

        logger.error(f"Все модели недоступны. Последняя ошибка: {last_error}")
        return self.fallback_responses[hash(user_message) % len(self.fallback_responses)]

ai_assistant = AIAssistant()

# Telegram обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👴 Дед Коля на связи! Используем модель: {ai_assistant.current_model}\n"
        "Шо надо, курва?"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = ai_assistant.generate_response(update.message.text)
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {str(e)}")
        await update.message.reply_text("Блядь, я сломался... Попробуй ещё раз!")

def init_telegram():
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
    return jsonify({
        "status": "running",
        "model": ai_assistant.current_model,
        "telegram": bool(telegram_app)
    })

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    try:
        init_telegram()
        webhook_url = f"{WEBHOOK_URL}/telegram_webhook"
        telegram_app.bot.set_webhook(webhook_url)
        return jsonify({
            "status": "success",
            "url": webhook_url,
            "active_model": ai_assistant.current_model
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

@app.route('/test_ai', methods=['GET'])
def test_ai():
    """Тестовый эндпоинт для проверки работы нейросети"""
    try:
        test_message = "Привет! Как дела?"
        response = ai_assistant.generate_response(test_message)
        
        return jsonify({
            "status": "success",
            "request": test_message,
            "response": response,
            "model": ai_assistant.current_model,
            "available_models": MODELS
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "models_tried": MODELS
        }), 500

def check_config():
    """Проверка обязательных настроек"""
    required_vars = {
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'OPENROUTER_API_KEY': OPENROUTER_API_KEY
    }
    
    missing = [name for name, val in required_vars.items() if not val]
    if missing:
        logger.error(f"Отсутствуют обязательные переменные: {', '.join(missing)}")
        return False
    
    logger.info("="*50)
    logger.info(f"TELEGRAM_TOKEN: {'установлен' if TELEGRAM_TOKEN else 'отсутствует'}")
    logger.info(f"OPENROUTER_API_KEY: {'установлен' if OPENROUTER_API_KEY else 'отсутствует'}")
    logger.info(f"WEBHOOK_URL: {WEBHOOK_URL or 'не установлен (будет использован polling)'}")
    logger.info(f"Доступные модели: {MODELS}")
    logger.info("="*50)
    return True

if __name__ == '__main__':
    if not check_config():
        exit(1)

    try:
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
            
    except Exception as e:
        logger.critical(f"Критическая ошибка: {str(e)}")
    finally:
        if telegram_app:
            telegram_app.stop()
            telegram_app.shutdown()
