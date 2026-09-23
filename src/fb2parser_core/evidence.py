# -*- coding: utf-8 -*-
"""Единый реестр источников данных (author_source/series_source) и
приоритет между ними — заготовка для устранения хрупкости эвристического
каскада regen_csv/pass2-6 (docs/quality-roadmap.md, баг №109, раздел
"размышление о хрупкости").

Раньше правило "Папка(3) > Файл(2) > Мета(1)" существовало только как
комментарий в нескольких местах — и было продублировано (слегка
по-разному) как минимум в трёх местах: `series_processor.py` (бинарное
high/low), `pass3_series_normalize.py` (своя 6-уровневая шкала), и
десятках мест вида `if not record.proposed_series: ...` (0 уровней —
просто "непусто = уже достаточно авторитетно", без проверки самого
источника). Аналогично, вычисление «есть ли у папки хоть какой-то
папочный сигнал о серии» было независимо реализовано трижды (baг №109,
продолжение — "Начинается вьюга"/"Хранитель 2 (Защитник тьмы)"):
`regen_csv.py::_postcheck_metadata_rescue()`,
`pass2_series_filename.py::execute()` и
`pass2_series_filename.py::_postpass_metadata_fallback()`.

Этот модуль — НЕ попытка переписать весь каскад разом (риск слишком
велик при ~11000 строк и ~415 существующих regression-тестах). Это
общая, переиспользуемая инфраструктура, куда постепенно, по одному
месту за раз, переводятся уже обнаруженные дубликаты — начиная с
`folder_has_signal()`, которым уже пользуются `regen_csv.py` и
`pass2_series_filename.py` вместо трёх независимых копий одного и того
же цикла.
"""
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Тир 3 — папочная структура. Создана человеком вручную (или её явное
# отсутствие подтверждено — "no_series_folder") — самый надёжный
# источник из всех. Точное множество трижды дублировалось байт-в-байт
# (regen_csv.py, дважды в pass2_series_filename.py) — сохранено здесь
# как есть, без добавления новых значений (напр. "folder_multiauthor" —
# это author_source из СОВСЕМ другого механизма, не входил ни в один из
# трёх дублей и НЕ должен сюда попасть, чтобы не изменить поведение).
FOLDER_SOURCES = frozenset({
    'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
    'folder_metadata_confirmed', 'no_series_folder',
})

# Тир 2 — извлечено из имени файла. Надёжнее метаданных (конвертеры и
# издатели чаще ошибаются в <sequence>, чем составитель библиотеки — в
# имени файла), но менее авторитетно, чем реальная структура папок.
_FILENAME_PREFIX = 'filename'

# Тир 1 — метаданные самого FB2 и любые вычисленные из них консенсусы
# по нескольким файлам. Наименее надёжный источник — автор/издатель/
# OCR-конвертер мог ошибиться, а голосование по нескольким файлам с
# ОДНОЙ и той же ошибкой ничего не докажет.
_METADATA_PREFIXES = ('metadata', 'consensus')


def source_tier(source: Optional[str]) -> int:
    """Вернуть числовой тир источника: 3 (папка) > 2 (файл) > 1 (мета/
    консенсус) > 0 (пусто/неизвестно).

    Составные источники вида "folder_dataset+series-consensus" или
    "filename+meta_expanded" (PASS4 дописывает суффикс через "+", когда
    расширяет/уточняет исходное значение) оцениваются по БАЗОВОЙ части
    до "+" — суффикс не меняет тир, только фиксирует, что значение было
    впоследствии подтверждено/расширено.
    """
    if not source:
        return 0
    base = source.split('+', 1)[0]
    if base in FOLDER_SOURCES:
        return 3
    if base.startswith(_FILENAME_PREFIX):
        return 2
    if base.startswith(_METADATA_PREFIXES):
        return 1
    return 0


def pick_winner(
    candidates: Sequence[Tuple[object, Optional[str]]],
) -> Optional[Tuple[object, Optional[str]]]:
    """Выбрать «победителя» среди кандидатов (value, source) по тиру
    источника (см. `source_tier`). Пустые (falsy) value игнорируются.
    При равенстве тиров побеждает ПЕРВЫЙ по порядку следования (стабильно
    — porядок задаёт вызывающий код, обычно это порядок павы/приоритет
    внутри одного тира не различается этим модулем).

    Возвращает None, если среди кандидатов нет ни одного непустого value.
    """
    best: Optional[Tuple[object, Optional[str]]] = None
    best_tier = -1
    for value, source in candidates:
        if not value:
            continue
        tier = source_tier(source)
        if tier > best_tier:
            best = (value, source)
            best_tier = tier
    return best


def folder_has_signal(
    records: Iterable[object],
    *,
    source_attr: str = 'series_source',
    path_attr: str = 'file_path',
) -> Dict[str, bool]:
    """Карта «папка (родитель file_path) → был ли хоть у одного файла в
    ней папочный сигнал» (значение `source_attr` записи входит в
    `FOLDER_SOURCES`).

    Используется, чтобы не позволять "последний шанс на голую
    metadata_series" (rescue-механизмы) придумывать серию для файла, чья
    папка НИКОГДА не давала папочного сигнала о серии вообще — реальный
    случай (docs/quality-roadmap.md, баг №109, продолжение): "Начинается
    вьюга.fb2" лежит прямо в корневой папке автора (23 самостоятельных
    рассказа, ни один не в подпапке серии), но собственные метаданные
    несут `<sequence name="Хроники Сиалы">` — настоящая серия, но
    существующая в ДРУГОМ месте библиотеки.

    Порядок записей имеет значение только в той мере, в какой более
    ранняя запись с папочным сигналом "включает" True для папки раньше —
    итоговый результат для полностью пройденного `records` от порядка не
    зависит (папка либо получает сигнал хоть от одной записи, либо нет).
    """
    result: Dict[str, bool] = {}
    for rec in records:
        path = getattr(rec, path_attr, None)
        if not path:
            continue
        folder = str(Path(path).parent)
        source = getattr(rec, source_attr, '') or ''
        if source in FOLDER_SOURCES:
            result[folder] = True
        elif folder not in result:
            result[folder] = False
    return result


def log_decision(record: object, message: str) -> None:
    """Добавить человекочитаемую запись в трассировку решений записи
    (размышление о хрупкости, часть 2 — docs/quality-roadmap.md, баг
    №109).

    Заменяет ручную археологию через `git stash` + разовый scratch-скрипт
    (именно так расследовались "Начинается вьюга"/"Демон"/"Хранитель 2
    (Защитник тьмы)" — см. историю в quality-roadmap.md): rescue/
    fallback-механизмы вызывают эту функцию в момент принятия решения
    ("восстановил серию из meta, потому что папка X дала сигнал" /
    "НЕ восстановил, потому что папка X сигнала не давала"), и позже
    можно посмотреть ВЕСЬ путь принятия решения одним вызовом
    `format_decision_log()`, не переигрывая пайплайн вручную.

    Молча ничего не делает, если у записи нет атрибута `decision_log`
    (например, лёгкие SimpleNamespace-обёртки для GUI-превью в
    fb2parser_web/views.py — трассировка для них не обязательна).
    """
    log = getattr(record, 'decision_log', None)
    if log is not None:
        log.append(message)


def format_decision_log(record: object) -> str:
    """Человекочитаемый дамп `record.decision_log` — одна строка на
    решение, в порядке накопления. Пустая трассировка — не ошибка, а
    просто "ни один rescue/fallback-механизм ничего не решал для этой
    записи" (обычно значит: серия/автор были определены раньше, папкой
    или именем файла, до того как дошло дело до rescue-каскада)."""
    log = list(getattr(record, 'decision_log', None) or [])
    if not log:
        return "(трассировка пуста — rescue/fallback-механизмы не вызывались для этой записи)"
    return "\n".join(f"{i + 1}. {entry}" for i, entry in enumerate(log))
