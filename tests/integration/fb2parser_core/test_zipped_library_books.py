"""Книги библиотеки в .fb2.zip (сервер 2026-10-28: 22 593 из 23 860 — почти
всё сжато). Имя сжатой книги разбиралось с хвостом «.fb2» — правила с концом
строки («Серия 1-13», «(в 12 книгах)», «… т. 4») молчали. Случай: Муравьев
«Перешагнуть пропасть» — полный чужой сборник «1-13 (компиляция).fb2.zip» не
распознавался как сборник томов 1–13 и лежал рядом с нашими частями.

Метаданные из архива разбор по-прежнему не читает: на прогоне по всей
библиотеке чтение меняло авторов («Урсула Ле Гуин» → «Ле Урсула») и брало
издательские серии — отдельная задача (docs/series-duplicates.md).
"""
import zipfile
from pathlib import Path

import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path
from tests.integration.fb2parser_core.test_third_party_omnibus import (
    _omnibus,
    _sync_compile,
)

pytestmark = pytest.mark.django_db
SERIES = "Перешагнуть пропасть"


def _zip(path: Path) -> Path:
    target = path.with_name(path.name + ".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, path.name)
    path.unlink()
    return target


def test_zipped_book_file_name_is_parsed_without_fb2_tail(tmp_path):
    folder = tmp_path / "Фантастика" / "Муравьев Константин" / SERIES
    omnibus = _zip(_omnibus(folder / f"Муравьев Константин - {SERIES}. 1-13 (компиляция).fb2",
                            f"Муравьёв. {SERIES} 1-13 (компиляция)", "Муравьёв Константин", [1, 2, 3]))
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    [rec] = [r for r in records if Path(r.file_path).name == omnibus.name]
    assert (rec.proposed_series, rec.series_number) == (SERIES, "1-13")


def test_complete_zipped_omnibus_is_kept_parts_go(tmp_path):
    """Правило 1 (решение пользователя 2026-10-27): самая полная по томам
    версия остаётся, части, чей текст в ней есть, — дубли."""
    author_dir = tmp_path / "Фантастика" / "Муравьев Константин"
    folder = author_dir / SERIES
    omnibus = _zip(_omnibus(folder / f"Муравьев Константин - {SERIES}. 1-5 (компиляция).fb2",
                            f"Муравьёв. {SERIES} 1-5 (компиляция)", "Муравьёв Константин", range(1, 6),
                            chapters=False))
    parts = [_zip(_omnibus(folder / f"Муравьев Константин - {SERIES} (т. {lo}-{hi}).fb2",
                           f"{SERIES} (т. {lo}-{hi})", "Муравьёв Константин", range(lo, hi + 1)))
             for lo, hi in ((1, 2), (3, 5))]
    _sync_compile(tmp_path, author_dir)
    # Оставленный сборник переименовывается по нашему образцу («… (Пенталогия)»);
    # это он, а не пересборка из частей: в частях главы с заголовками, в нём — нет.
    [kept] = list(folder.iterdir())
    assert not any(p.exists() for p in parts) and kept.name != omnibus.name
    with zipfile.ZipFile(kept) as z:
        assert "Глава" not in z.read(z.namelist()[0]).decode("utf-8")
