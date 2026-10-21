"""Ветка «Книги, выпущенные издательством» и соавтор из имени файла —
docs/quality-roadmap.md, баг №175.

- В «СЕРИЯ.LitRPG» две ветки: «Hеофициальная серия книг (разные
  издательства)» и «Книги, выпущенные издательством». В настройках
  (author_subfolder_collections, genre_folder_prefixes) была только первая —
  у 15 книг второй ветки имя служебной папки стало серией или её корнем
  («Книги, выпущенные издательством\\Темный паладин»); подцикл Кучеренко
  «Хроники Ала-Арна» распался, книга 2 вставала гостем в «Миры Бессмертных».
- «2. Трудно быть мобом (соавт. Ольховская-Инга).fb2» — соавтор назван в
  имени файла и есть в метаданных, но голосование по серии молчало.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
{authors}
<book-title>{title}</book-title>
<sequence name="{seq}" number="{sn}"/>
</title-info>
</description>
<body><section><p>{text}</p></section></body>
</FictionBook>
"""

_A = "<author><first-name>Владимир</first-name><last-name>Кучеренко</last-name></author>"
_B = "<author><first-name>Инга</first-name><last-name>Ольховская</last-name></author>"


def _write(root, branch, rel, seq, sn, authors=_A):
    path = root / "СЕРИЯ.LitRPG" / branch / "Кучеренко-Владимир" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = " ".join(f"{path.stem[:8]}слово{j}" for j in range(400))
    path.write_text(_FB2.format(authors=authors, title=path.stem, seq=seq, sn=sn, text=text), encoding="utf-8")


@pytest.fixture
def short_root():
    """Короткий корень вместо tmp_path: путь pytest
    (…/pytest-of-<user>/pytest-N/<имя теста>0/) плюс настоящие длинные имена
    папок этой ветки давали ровно 260 символов — больше предела Windows
    без LongPathsEnabled (FileNotFoundError при записи файла)."""
    root = Path(tempfile.mkdtemp(prefix="pbc"))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_publisher_branch_and_filename_coauthor(short_root):
    tmp_path = short_root
    neo, pub = "Hеофициальная серия книг (разные издательства)", "Книги, выпущенные издательством"
    for n, name in ((1, "Алебардист"), (2, "Головоломка"), (3, "Синергия")):
        _write(tmp_path, neo, f"Миры Бессмертных/0{n} - {name}.fb2", "Миры Бессмертных", n)
    _write(tmp_path, pub, "Миры Бессмертных/Хроники Ала-Арна/1. Бета-тестеры поневоле (соавт. Ольховская-Инга).fb2",
           "Хроники Ала-Арна", 1, _B + _A)
    _write(tmp_path, neo, "Миры Бессмертных/Хроники Ала-Арна/2. Трудно быть мобом (соавт. Ольховская-Инга).fb2",
           "Миры Бессмертных", 2, _A + _B)
    _write(tmp_path, pub, "Темный паладин/1. Темный паладин.fb2", "Темный паладин", 1)

    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    assert not [r.proposed_series for r in records if "Книги, выпущенные" in r.proposed_series]
    ala = [r for r in records if "Ала-Арна" in r.file_path]
    assert {r.proposed_author for r in ala} == {"Кучеренко Владимир, Ольховская Инга"}

    groups = {g.series: g for g in FB2CompilerService().find_groups(records, tmp_path) if g.books}
    assert [b.volume_label for b in groups["Миры Бессмертных"].books] == ["1", "2", "3"]
    assert [b.abs_path.name[:2] for b in groups["Миры Бессмертных\\Хроники Ала-Арна"].books] == ["1.", "2."]
