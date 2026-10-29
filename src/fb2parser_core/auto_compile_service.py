"""Сервис автоматической компиляции всех групп в папке библиотеки."""
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from fb2parser_core.fb2_compiler import CompilationGroup, FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService


def find_library_groups(
    library_path: str,
    config_path: Optional[str] = None,
    filter_paths: Optional[set] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[FB2CompilerService, List[CompilationGroup]]:
    """Разобрать папку и найти группы компилятора — без изменений на диске.

    Общая часть автокомпиляции и пробного прогона чистки библиотеки
    (`manage.py library_cleanup`): решения у них одни и те же.
    """
    _devnull = open(os.devnull, 'w', encoding='utf-8')
    _old_out, _old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = _devnull
    try:
        svc_csv = RegenCSVService(config_path=config_path) if config_path else RegenCSVService()
        records = svc_csv.generate_csv(library_path, output_csv_path=None, filter_paths=filter_paths,
                                       progress_callback=progress_callback)
        if not records:
            records = getattr(svc_csv, 'records', []) or []
    finally:
        sys.stdout, sys.stderr = _old_out, _old_err
        _devnull.close()

    compiler = FB2CompilerService()
    return compiler, compiler.find_groups(records, Path(library_path))


def group_genre(group: CompilationGroup, library_path: Path) -> str:
    """Жанр итогового файла — из genre-папки библиотеки (первый сегмент пути
    группы относительно library_path), а не только из <genre> метаданных
    первой исходной книги: та часто пуста у файлов-кандидатов на компиляцию
    ("Дилогия"/"в N книгах"), из-за чего компилятор жёстко подставлял
    заглушку "other", хотя файлы физически уже лежат в правильной жанровой
    папке (см. compile_group()'s genre_override и docs/quality-roadmap.md).
    Вызывающий код всегда передаёт реальную, genre-организованную
    библиотеку — первый сегмент относительного пути всегда genre-папка."""
    if not group.books:
        return ''
    try:
        rel_parts = group.books[0].abs_path.resolve().relative_to(library_path.resolve()).parts
    except ValueError:
        return ''
    return rel_parts[0] if rel_parts else ''


def auto_compile_library(
    library_path: str,
    on_group: Optional[Callable[[str, str, bool], None]] = None,
    config_path: Optional[str] = None,
    filter_paths: Optional[set] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Сгенерировать CSV, найти группы и скомпилировать каждую с удалением исходников.

    Args:
        library_path: путь к папке (библиотеки или произвольной исходной папке —
            функция не привязана к структуре библиотеки, ``compile_group`` пишет
            результат рядом с исходниками группы).
        on_group: callback(author, series, success) — после каждой группы.
        config_path: путь к config.json; если None — используется дефолтный.
        filter_paths: опциональный набор абсолютных путей подпапок — если задан,
            обрабатываются только файлы внутри них (как в ``synchronize()``).
        progress_callback: callback(current, total, status) — во время разбора
            и перед каждой группой; исключение из него (InterruptedError —
            «Стоп» синхронизации) прерывает компиляцию между группами: каждая
            группа компилируется целиком или не трогается.

    Returns:
        dict с ключами ok (int), fail (int).
    """
    compiler, groups = find_library_groups(library_path, config_path, filter_paths, progress_callback)
    lib_path = Path(library_path)

    ok_cnt = 0
    fail_cnt = 0
    for i, g in enumerate(groups):
        if progress_callback:
            progress_callback(i, len(groups), f"Компиляция: {g.author} — {g.series}")
        result = compiler.compile_group(g, None, delete_sources=True, genre_override=group_genre(g, lib_path))
        if result.success:
            ok_cnt += 1
        else:
            fail_cnt += 1
        if on_group:
            # Баг №109 (продолжение): с display_root в логе видно "Мир
            # Вальдиры. Кроу", а не голое "Кроу" без контекста.
            on_group(g.author, compiler._series_to_display(
                compiler._group_series_for_naming(g)), result.success)

    return {'ok': ok_cnt, 'fail': fail_cnt}
