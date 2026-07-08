"""
Модуль для нормализации серий и текстовых данных.

Содержит классы и функции для стандартизации названий серий,
нормализации текста и сравнения.
"""

import re
import unicodedata
from typing import Optional


def _nfc_lower_yo(s: str) -> str:
    """
    NFC-нормализация + lower + ё→е.

    NFC нужна: если строка в NFD-форме, ё = е + U+0308, и replace('ё','е') не работает.
    """
    return unicodedata.normalize('NFC', s).lower().replace('\u0451', '\u0435')


class SeriesNormalizer:
    """
    Класс для нормализации названий серий и текстовых данных.
    """

    def __init__(self):
        self._series_norm_cache: dict = {}

    def normalize_series_for_consensus(self, series_candidate: str) -> str:
        """
        Нормализовать кандидата серии для сравнения консенсуса.
        Убирает номера томов, чтобы "Охотник 1" и "Охотник 2" совпадали как одна серия.

        Args:
            series_candidate: Исходная строка кандидата серии

        Returns:
            Нормализованное название серии
        """
        if not series_candidate:
            return ""

        cached = self._series_norm_cache.get(series_candidate)
        if cached is not None:
            return cached

        text = series_candidate.strip()

        # Убрать " N" или " N. " паттерны (пробел + цифры).
        # НЕ трогать если число — часть десятичной версии: "Цивилизация 2.0", "Metro 2.0".
        # Признак версии: после числа сразу идёт точка и ещё цифра (lookahead (?!\.\d)).
        # "Охотник 1"       → "Охотник"
        # "Охотник 2. ..."  → "Охотник"
        # "Цивилизация 2.0" → "Цивилизация 2.0"  (не трогать)
        text = re.sub(r'\s+\d+(?!\.\d)[\s\.]*$', '', text).strip()

        # Убрать цифры после пробела (дополнительный проход)
        text = re.sub(r'\s+\d+(?!\.\d)\s*$', '', text).strip()

        # Убрать цифры после дефиса ("Фэндом-3" → "Фэндом"), не трогать "Серия-2.0"
        text = re.sub(r'[-–—]\d+(?!\.\d)\s*$', '', text).strip()

        result = text if text else series_candidate
        self._series_norm_cache[series_candidate] = result
        return result

    def normalize_text(self, text: str) -> str:
        """
        Общая нормализация текста: NFC + lower + ё→е.

        Args:
            text: Исходный текст

        Returns:
            Нормализованный текст
        """
        return _nfc_lower_yo(text)

    def clear_cache(self):
        """Очистить кэш нормализаций."""
        self._series_norm_cache.clear()