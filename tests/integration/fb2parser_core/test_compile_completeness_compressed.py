"""Регрессия для fb2_compiler.find_groups()'s "лучшая предкомпиляция"
эвристики (тот же реальный случай "Странник", что и
test_compile_completeness.py) — обнаружено при проверке смешанных данных
(docs/quality-roadmap.md, баг №20, часть 3): выбор между широким, но
урезанным изданием и набором более полных узких предкомпиляций сравнивал
`.stat().st_size` НАПРЯМУЮ. Если один из кандидатов оказывается сжатым
`.fb2.zip` (см. функцию "Сжать" в Library), его размер на диске падает
на порядки относительно реального содержимого — сравнение по байтам на
диске могло полностью перевернуть решение (оставить урезанное издание
вместо действительно более полных частей).

Использует ту же fixture (tests/data/compile_completeness/), что и
test_compile_completeness.py, но сжимает более полные узкие части
("1-3"/"4-5") перед прогоном — на КОПИИ во временной директории, чтобы не
трогать закоммиченные данные.
"""
import shutil
from pathlib import Path

from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.fb2_utils import compress_fb2_file
from fb2parser_web.fb2parser_bridge import _config_path

LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "data" / "compile_completeness"


def test_fuller_narrower_precompiles_still_win_when_compressed(tmp_path):
    work_dir = tmp_path / "compile_completeness"
    shutil.copytree(LIBRARY_ROOT, work_dir)
    author_dir = work_dir / "Автор Тест"

    # Сжимаем более полные (реально бОльшие по контенту) узкие части — на
    # диске они станут в разы МЕНЬШЕ, чем оставшееся плоским урезанное
    # "Пенталогия"-издание, хотя реальное содержимое по-прежнему больше.
    compress_fb2_file(author_dir / "Автор Тест - Серия Тест (Серия Тест 1-3).fb2")
    compress_fb2_file(author_dir / "Автор Тест - Серия Тест (Серия Тест 4-5).fb2")

    service = regen_csv.RegenCSVService(_config_path())
    records = service.generate_csv(str(work_dir), output_csv_path=None)
    svc = FB2CompilerService()
    groups = svc.find_groups(records, work_dir)
    matches = [g for g in groups if g.author == "Автор Тест"]
    assert len(matches) == 1
    group = matches[0]

    kept_names = {p.name for p in (group.kept_paths or [])}
    dup_names = {p.name for p in (group.duplicate_paths or [])}
    book_names = {b.abs_path.name for b in group.books}

    assert "Автор Тест - Серия Тест (Серия Тест. Пенталогия).fb2" not in kept_names
    assert "Автор Тест - Серия Тест (Серия Тест. Пенталогия).fb2" in dup_names
    assert "Автор Тест - Серия Тест (Серия Тест 1-3).fb2.zip" in book_names
    assert "Автор Тест - Серия Тест (Серия Тест 4-5).fb2.zip" in book_names
