"""
Logging Module / Модуль логирования

Handles logging of actions and errors.

/ Логирование действий и ошибок.
"""
from collections import deque
from datetime import datetime

class Logger:
    """
    Simple in-memory logger (capped at 10,000 entries to limit memory use).
    
    / Простой логгер в памяти.
    """
    
    def __init__(self):
        """Initialize logger / Инициализация логгера."""
        self.entries = deque(maxlen=10000)

    def log(self, message):
        """
        Log a message.
        
        / Залогировать сообщение.
        """
        entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.entries.append(entry)

