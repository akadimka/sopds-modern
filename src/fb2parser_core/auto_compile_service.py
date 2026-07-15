"""Сервис автоматической компиляции всех групп в папке библиотеки."""
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_core.fb2_compiler import FB2CompilerService


def auto_compile_library(
    library_path: str,
    on_group: Optional[Callable[[str, str, bool], None]] = None,
    config_path: Optional[str] = None,
    filter_paths: Optional[set] = None,
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

    Returns:
        dict с ключами ok (int), fail (int).
    """
    _devnull = open(os.devnull, 'w', encoding='utf-8')
    _old_out, _old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = _devnull
    try:
        svc_csv = RegenCSVService(config_path=config_path) if config_path else RegenCSVService()
        records = svc_csv.generate_csv(library_path, output_csv_path=None, filter_paths=filter_paths)
        if not records:
            records = getattr(svc_csv, 'records', []) or []
    finally:
        sys.stdout, sys.stderr = _old_out, _old_err
        _devnull.close()

    compiler = FB2CompilerService()
    groups = compiler.find_groups(records, Path(library_path))

    ok_cnt = 0
    fail_cnt = 0
    for g in groups:
        result = compiler.compile_group(g, None, delete_sources=True)
        if result.success:
            ok_cnt += 1
        else:
            fail_cnt += 1
        if on_group:
            on_group(g.author, g.series, result.success)

    return {'ok': ok_cnt, 'fail': fail_cnt}
