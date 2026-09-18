"""Облегчённый пайплайн только для извлечения авторов (без серий/жанров).

Баг №105: раньше модуль также определял `run_author_only_pipeline()`
(Precache+Pass1+Pass2+Pass2Fallback без серий/жанров) и
`collect_unknown_gender_authors()` — ни один внешний вызывающий к ним не
обращался. Убраны целиком (см. docs/quality-roadmap.md, баг №105).
Осталась только `guess_first_name()` (используется
`fb2parser_web/views.py`).
"""


def guess_first_name(author: str, author_source: str) -> str:
    """Угадать имя автора по формату источника.

    Источник 'filename' хранит автора в западном порядке «Имя Фамилия»,
    все остальные — в русском «Фамилия Имя».
    """
    parts = author.split()
    if not parts:
        return ""
    if author_source == "filename":
        return parts[0] if len(parts) >= 2 else ""  # западный порядок: первое слово = имя
    return parts[1] if len(parts) >= 2 else ""      # русский порядок: второе слово = имя
