from flask import Flask, request, jsonify, render_template
from brain.memory import Memory
from brain.personality import Personality
import os

app = Flask(__name__)
app.config['DATABASE'] = 'db/sessions.db'

# Инициализация
memory = Memory(app.config['DATABASE'])
persona = Personality()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_id = request.json.get('user_id', 'default')
    user_input = request.json.get('message', '').strip()
    
    if not user_input:
        return jsonify({"error": "Пустое сообщение"}), 400
    
    # Получаем историю и состояние
    history = memory.get_history(user_id)
    mood = memory.get_mood(user_id)
    
    # Генерируем ответ
    response, new_mood = persona.generate_response(
        user_input=user_input,
        history=history,
        current_mood=mood
    )
    
    # Сохраняем в память
    memory.save_interaction(
        user_id=user_id,
        user_message=user_input,
        bot_response=response,
        mood=new_mood
    )
    
    return jsonify({
        "response": response,
        "mood": new_mood
    })

if __name__ == '__main__':
    app.run(debug=True)
