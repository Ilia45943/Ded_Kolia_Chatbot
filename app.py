import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from brain.memory import Memory
from brain.personality import Personality

# Инициализация
memory = Memory('db/sessions.db')
persona = Personality()
TOKEN = os.getenv('TELEGRAM_TOKEN')  # Добавишь в настройках Render

async def handle_message(update: Update, context):
    user_id = str(update.message.from_user.id)
    user_input = update.message.text
    
    # Получаем историю и настроение
    history = memory.get_history(user_id)
    mood = memory.get_mood(user_id)
    
    # Генерируем ответ
    response, new_mood = persona.generate_response(
        user_input=user_input,
        history=history,
        current_mood=mood
    )
    
    # Сохраняем и отправляем
    memory.save_interaction(user_id, user_input, response, new_mood)
    await update.message.reply_text(response)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.run_polling()  # Или вебхук (см. ниже)

if __name__ == '__main__':
    main()
