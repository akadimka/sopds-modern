"""Проверка «путь внутри папки» — одна на весь проект.

Пути приходят из запросов, JSON от клиента, БД (INPX) и метаданных FB2,
поэтому перед чтением/записью/удалением/перемещением проверяется, что
итоговый путь не выходит за разрешённую папку. Сравнение по
`os.path.commonpath` после `realpath` (симлинки, `..`) и `normcase`
(регистр на Windows) — не `str.startswith`: "C:\\Lib2" начинается с
"C:\\Lib", но в ней не лежит.
"""
import os


def is_within(path, folder) -> bool:
    """True, если `path` — это `folder` или лежит внутри неё."""
    if not folder or not path:
        return False
    try:
        real_folder = os.path.normcase(os.path.realpath(folder))
        real_path = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([real_folder, real_path]) == real_folder
    except (OSError, ValueError):
        # ValueError: пути на разных дисках Windows
        return False
