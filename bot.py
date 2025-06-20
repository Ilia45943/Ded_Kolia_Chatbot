import os
import sqlite3
import requests
import random
import re
import threading
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters
from flask import Flask, request

# ====================== КОНФИГУРАЦИЯ ======================
TOKEN = os.getenv('TELEGRAM_TOKEN')
AI21_API_KEY = os.getenv('AI21_API_KEY')
PORT = int(os.environ.get('PORT', 5000))  # Render сам назначает порт

# Инициализация Flask app
flask_app = Flask(__name__)

# ====================== БАЗА ЗНАНИЙ ДЕДА КОЛИ ======================
class KnowledgeBase:
    def __init__(self, db_path="/tmp/knowledge.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                fact TEXT NOT NULL,
                value TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS general_knowledge (
                id INTEGER PRIMARY KEY,
                topic TEXT NOT NULL,
                fact TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    
    def add_user_fact(self, user_id, fact, value):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_facts (user_id, fact, value, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, fact, value, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_user_facts(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fact, value 
            FROM user_facts 
            WHERE user_id = ?
            ORDER BY timestamp DESC
        """, (user_id,))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def add_general_knowledge(self, topic, fact):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO general_knowledge (topic, fact, timestamp)
            VALUES (?, ?, ?)
        """, (topic, fact, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_related_knowledge(self, topic):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fact 
            FROM general_knowledge 
            WHERE topic LIKE ?
            ORDER BY timestamp DESC
            LIMIT 3
        """, (f'%{topic}%',))
        result = [row[0] for row in cursor.fetchall()]
        conn.close()
        return result

# ====================== МОДУЛЬ ПАМЯТИ ======================
class Memory:
    def __init__(self, db_path="/tmp/sessions.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                user_message TEXT,
                bot_response TEXT,
                mood TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    
    def get_history(self, user_id, limit=6):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_message, bot_response 
            FROM sessions 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (user_id, limit))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_mood(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT mood 
            FROM sessions 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT 1
        """, (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else "neutral"
    
    def save_interaction(self, user_id, user_message, bot_response, mood):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions 
            (user_id, timestamp, user_message, bot_response, mood) 
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            datetime.now().isoformat(),
            user_message,
            bot_response,
            mood
        ))
        conn.commit()
        conn.close()

# ====================== ХАРАКТЕР ДЕДА КОЛИ ======================
class Personality:
    def __init__(self, knowledge_base):
        self.api_key = AI21_API_KEY
        self.kb = knowledge_base
        self.base_prompt = """
        Ты — Дед Коля (67 лет). Характер:
        - Матерый, саркастичный старик
        - Чёрный юмор, матерный язык
        - Любит: бухло, трактор, Люду, Федю
        - Фразы: "курва", "ебать в рот", "пьянь ходячая", "чек, коровы идут"
        - История: собрал трактор, ездит на Опеле Астре 96-го года
        
        Текущее настроение: {mood}
        
        Известное о собеседнике:
        {user_facts}
        
        Контекст диалога:
        {context}
        """
    
    def _determine_mood(self, user_input: str) -> str:
        triggers = {
            "happy": ["спасибо", "класс", "люблю", "хорош"],
            "angry": ["дурак", "идиот", "ненавижу", "скучно"]
        }
        
        input_lower = user_input.lower()
        if any(word in input_lower for word in triggers["happy"]):
            return "happy"
        elif any(word in input_lower for word in triggers["angry"]):
            return "angry"
        return random.choice(["neutral", "sarcastic", "drunk"])
    
    def _extract_and_save_facts(self, user_id, user_input):
        patterns = {
            "имя": r"(меня зовут|мое имя|зовут меня) ([а-яА-ЯёЁ]+)",
            "город": r"(я из|живу в|город) ([а-яА-ЯёЁ\s]+)",
            "возраст": r"(мне|исполнилось|возраст) (\d{1,2}) (года|лет)",
            "работа": r"(я работаю|моя работа|профессия) ([а-яА-ЯёЁ\s]+)",
            "хобби": r"(мои хобби|увлекаюсь|люблю) ([а-яА-ЯёЁ\s\,]+)"
        }
        
        for fact_type, pattern in patterns.items():
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                value = match.group(2).strip()
                self.kb.add_user_fact(user_id, fact_type, value)
                return f"Окей, запомнил что твоё {fact_type} - {value}!"
        
        return None

    def generate_response(self, user_id, user_input: str, history: list, current_mood: str) -> tuple:
        learn_result = self._extract_and_save_facts(user_id, user_input)
        if learn_result:
            return learn_result, "neutral"
        
        new_mood = self._determine_mood(user_input)
        
        user_facts = self.kb.get_user_facts(user_id)
        facts_str = "\n".join([f"- {fact[0]}: {fact[1]}" for fact in user_facts[:3]]) if user_facts else "Ничего не известно"
        
        context_lines = []
        for user_msg, bot_msg in history[-3:]:
            context_lines.append(f"User: {user_msg}")
            context_lines.append(f"Дед Коля: {bot_msg}")
        
        context = "\n".join(context_lines)
        
        full_prompt = self.base_prompt.format(
            mood=new_mood,
            user_facts=facts_str,
            context=context
        ) + f"\nUser: {user_input}\nДед Коля:"
        
        try:
            response = requests.post(
                "https://api.ai21.com/studio/v1/jamba-instruct/complete",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "jamba-1.5",
                    "prompt": full_prompt,
                    "temperature": 0.85,
                    "maxTokens": 250,
                    "stopSequences": ["\nUser:"]
                },
                timeout=30
            )
            
            if response.status_code == 200:
                bot_response = response.json()['completions'][0]['data']['text']
            else:
                bot_response = f"Ой, курва, ошибка API! Код: {response.status_code}"
        except Exception as e:
            bot_response = f"Чёрт, сломалось: {str(e)}"
        
        return bot_response, new_mood

# ====================== ИНИЦИАЛИЗАЦИЯ СИСТЕМЫ ======================
knowledge_base = KnowledgeBase()
memory = Memory()
persona = Personality(knowledge_base)

# ====================== ТЕЛЕГРАМ БОТ ======================
def start_bot():
    print("⚙️ Инициализация бота Деда Коли...")
    app = Application.builder().token(TOKEN).build()
    
    # Регистрация обработчиков
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("teach", teach_command))
    
    print("🤖 Бот запущен в режиме polling...")
    app.run_polling()

# Обработчики команд
async def remember_command(update: Update, context):
    user_id = str(update.message.from_user.id)
    if not context.args:
        await update.message.reply_text("Чё запоминать-то? Используй: /remember я люблю пиво")
        return
    
    fact_text = " ".join(context.args)
    knowledge_base.add_user_fact(user_id, "факт", fact_text)
    await update.message.reply_text(f"Окей, курва, запомнил: {fact_text}")

async def teach_command(update: Update, context):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Используй: /teach трактор 'Т-25 ездит на солярке'")
        return
    
    topic = context.args[0]
    fact = " ".join(context.args[1:])
    knowledge_base.add_general_knowledge(topic, fact)
    await update.message.reply_text(f"Записал в базу знаний: {topic} - {fact}")

# Обработчик сообщений
async def handle_message(update: Update, context):
    user_id = str(update.message.from_user.id)
    user_input = update.message.text
    
    history = memory.get_history(user_id)
    mood = memory.get_mood(user_id)
    
    response, new_mood = persona.generate_response(
        user_id=user_id,
        user_input=user_input,
        history=history,
        current_mood=mood
    )
    
    memory.save_interaction(user_id, user_input, response, new_mood)
    await update.message.reply_text(response)

# ====================== FLASK РОУТЫ ======================
@flask_app.route('/')
def home():
    return "Дед Коля в работе! Бот запущен и работает."

@flask_app.route('/health')
def health_check():
    return "OK", 200

# ====================== ЗАПУСК ПРИЛОЖЕНИЯ ======================
if __name__ == '__main__':
    # Запускаем бот в отдельном потоке
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # Запускаем Flask сервер
    flask_app.run(host='0.0.0.0', port=PORT)
