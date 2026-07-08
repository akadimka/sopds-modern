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

    def get_entries(self):
        """
        Get last 1000 log entries.
        
        / Получить последние 1000 записей логов.
        """
        entries_list = list(self.entries)
        return entries_list[-1000:]

    def clear(self):
        """
        Clear all log entries.
        
        / Очистить все записи логов.
        """
        self.entries.clear()
