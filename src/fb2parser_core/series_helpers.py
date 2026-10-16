"""
Модуль с вспомогательными функциями для обработки серий.

Содержит утилиты для работы с текстом, авторами, паттернами и т.д.
"""

import re

_FORMAT_VARIANT_RE = re.compile(
    r'(?<![а-яёa-z])(?:лп|си|самиздат\w*|черновик\w*|чернов\w+)(?![а-яёa-z])', re.IGNORECASE)
_CONTENT_VARIANT_RE = re.compile(
    r'альтернатив|верси|перезагрузк|ребилд|rebuild|продолжени', re.IGNORECASE)


def is_content_variant_folder(folder_name: str) -> bool:
    """Папка-вариант с ДРУГИМ текстом — «Версия 2.0», «Возвращение
    (альтернативное продолжение)», «Перезагрузка», «Ребилд» — или серия, в
    имени которой есть такое слово («Проект Альтернативная Земля»,
    «Бета-версия»): самостоятельный уровень серии, не «тот же том в другом
    издании». Форматные варианты («СИ», «ЛП», «самиздат», «черновик») — нет.

    Баг №157 (аудит LitRPG, решение пользователя: варианты — отдельная серия).
    """
    name = (folder_name or '').replace('ё', 'е')
    return bool(_CONTENT_VARIANT_RE.search(name)) and not _FORMAT_VARIANT_RE.search(name)


def _bl_matches(bl: str, text: str, multi_word_series: bool = False) -> bool:
    """
    Проверить совпадение blacklist слова в тексте.

    Для многословных серий (2+ слов) требуется точное совпадение всей строки.
    Для коротких blacklist записей (< 4 символов) требуются границы слов.
    """
    if multi_word_series:
        return bl == text.strip()
    if len(bl) < 4:
        return bool(re.search(r'(?<![\w\u0430-\u044f\u0451a-z])' + re.escape(bl) + r'(?![\w\u0430-\u044f\u0451a-z])', text, re.IGNORECASE))
    return bl in text

