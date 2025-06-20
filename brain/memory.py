import sqlite3
from datetime import datetime

class Memory:
    def __init__(self, db_path):
        self.db_path = "/tmp/sessions.db"
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    user_message TEXT,
                    bot_response TEXT,
                    mood TEXT NOT NULL
                )
            """)
            conn.commit()
    
    def get_history(self, user_id, limit=6):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT user_message, bot_response 
                FROM sessions 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (user_id, limit))
            return cursor.fetchall()
    
    def get_mood(self, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT mood 
                FROM sessions 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (user_id,))
            result = cursor.fetchone()
            return result[0] if result else "neutral"
    
    def save_interaction(self, user_id, user_message, bot_response, mood):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
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
