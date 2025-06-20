import requests
import os
import random

class Personality:
    def __init__(self):
        self.api_key = os.getenv('AI21_API_KEY')
        self.base_prompt = """
        Ты — Дед Коля (67 лет). Характер:
        - Матерый, саркастичный
        - Чёрный юмор, матерный
        - Любит: бухло, трактор, Люду
        - Ненавидит: глупость, города
        - Фразы: "курва", "ебать в рот", "пьянь ходячая"
        Текущее настроение: {mood}
        Контекст: {context}
        """
    
    def _determine_mood(self, user_input: str) -> str:
        triggers = {
            "happy": ["спасибо", "класс", "люблю"],
            "angry": ["дурак", "идиот", "ненавижу"]
        }
        
        input_lower = user_input.lower()
        if any(word in input_lower for word in triggers["happy"]):
            return "happy"
        elif any(word in input_lower for word in triggers["angry"]):
            return "angry"
        return random.choice(["neutral", "sarcastic", "drunk"])

    def generate_response(self, user_input: str, history: list, current_mood: str) -> tuple:
        new_mood = self._determine_mood(user_input)
        context = "\n".join([f"User: {msg[0]}\nДед Коля: {msg[1]}" for msg in history[-3:]])
        
        full_prompt = self.base_prompt.format(
            mood=new_mood,
            context=context
        ) + f"\nUser: {user_input}\nДед Коля:"
        
        response = requests.post(
            "https://api.ai21.com/studio/v1/jamba-instruct/complete",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": "jamba-1.5",
                "prompt": full_prompt,
                "temperature": 0.8,
                "maxTokens": 250
            }
        )
        
        bot_response = response.json()['completions'][0]['data']['text']
        return bot_response, new_mood
