"""Имя файла книги в том виде, в каком его разбирает пайплайн regen.

Разбор имён везде ожидает разделитель « - » между автором и названием.
В части коллекций дефис приклеен к точке инициала — «Ахманов М.- Миссия
Тревельяна 1. Посланец небес»; без нормализации автором становился весь
кусок до точки после номера, а серия не извлекалась. Все места пайплайна,
читающие имя файла для разбора, должны брать его отсюда, чтобы видеть
одно и то же имя (иначе одно место сравнивает исходную строку с
результатом другого, уже нормализованного).

Для имён без такой записи функции возвращают ровно то же, что
Path(...).stem / Path(...).name.
"""
import re
from pathlib import Path

_INITIAL_DOT_DASH = re.compile(r"(\b[A-ZА-ЯЁ]\.)-(?=\s)")


def normalize_stem(stem: str) -> str:
    return _INITIAL_DOT_DASH.sub(r"\1 -", stem)


def file_stem(file_path) -> str:
    return normalize_stem(Path(file_path).stem)


def file_name(file_path) -> str:
    path = Path(file_path)
    return normalize_stem(path.stem) + path.suffix
