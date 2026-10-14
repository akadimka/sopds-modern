"""Одноимённые файлы в разных подциклах — разные книги (баг №152).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md): Шаман Иван «100 лет
апокалипсиса» — подциклы в подпапках «3. Универсал» и «4. Демон Разума»,
в обеих первый том назван «1. Часть первая.fb2». Подциклы компилируются
одной группой (позиции 3.1, 4.1…), а дедупликация по имени файла в `_emit`
сравнивала только имя: меньший из двух файлов уходил в дубли (после №141 —
в исключённые), и в сборнике у «Демона Разума» не было тома 1.

Теперь одноимённые файлы — кандидаты в дубли, только если стоят на одной
позиции группы (копии одной книги); дубль по-прежнему подтверждается
текстом (№141).
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Иван</first-name><last-name>Шаман</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("истребитель орк зооморф оттепель кадет вольный демон разум культ свет "
          "царство город пустошь отряд выстрел броня мутант лагерь рейд база").split()
ROOT = "100 лет апокалипсиса"


def _text(seed: int) -> str:
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))


def _book(root: Path, sub: str, name: str, n: int, title: str, seed: int) -> BookRecord:
    rel = f"{ROOT}/{sub}/{name}"
    (root / ROOT / sub).mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(_FB2.format(title=title, text=_text(seed)), encoding="utf-8")
    return BookRecord(
        file_path=rel, file_title=title,
        metadata_authors="Иван Шаман", proposed_author="Шаман Иван",
        author_source="folder_dataset", metadata_series=ROOT,
        proposed_series=f"{ROOT}\\{sub}", series_source="folder_dataset+subfolder_hierarchy",
        series_number=str(n), series_number_source="filename_prefix",
    )


def _records(root: Path, same_text: bool = False):
    return [
        _book(root, "3. Универсал", "1. Часть первая.fb2", 1, "Универсал. Часть первая", 31),
        _book(root, "3. Универсал", "2. Кадет.fb2", 2, "Универсал 2: Кадет", 32),
        _book(root, "3. Универсал", "3. Вольный.fb2", 3, "Вольный", 33),
        _book(root, "4. Демон Разума", "1. Часть первая.fb2", 1, "Демон Разума", 31 if same_text else 41),
        _book(root, "4. Демон Разума", "2. Культ света.fb2", 2, "Демон Разума 2: Культ света", 42),
        _book(root, "4. Демон Разума", "3. Царство света.fb2", 3, "Демон Разума 3: Царство света", 43),
    ]


def _rel(root, p):
    return Path(p).relative_to(root / ROOT).as_posix()


def test_first_volumes_of_two_subcycles_are_both_compiled(tmp_path):
    groups = [g for g in FB2CompilerService().find_groups(_records(tmp_path), tmp_path)
              if g.author == "Шаман Иван"]

    compiled = {_rel(tmp_path, b.abs_path) for g in groups for b in g.books}
    assert {"3. Универсал/1. Часть первая.fb2", "4. Демон Разума/1. Часть первая.fb2"} <= compiled
    assert not any(g.duplicate_paths or g.excluded_paths for g in groups)


def test_copy_of_the_same_volume_in_another_folder_is_compiled_once(tmp_path):
    """Копия того же тома (та же позиция, тот же текст) в другой папке в
    сборник второй раз не попадает."""
    recs = _records(tmp_path)[:3]
    copy = _book(tmp_path, "3. Универсал/FB2", "1. Часть первая.fb2", 1, "Универсал. Часть первая", 31)
    groups = [g for g in FB2CompilerService().find_groups(recs + [copy], tmp_path)
              if g.author == "Шаман Иван"]

    compiled = [_rel(tmp_path, b.abs_path) for g in groups for b in g.books]
    assert sum(p.endswith("1. Часть первая.fb2") for p in compiled) == 1
