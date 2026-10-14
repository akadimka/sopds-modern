"""Побочный цикл в подпапке — не «гостевой подцикл» родителя (баг №154).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md): Емельянов Антон, Савинов
Сергей «Царь зверей» 1–8 и подпапка «Мир Царя зверей» — «1. На полную
мощность (сюжет после 3 части Царя зверей)» и «2. Сборник рассказов…»
(без номера в записи). Regen верно дал подсерию «Царь зверей\\Мир Царя
зверей», но предпроход «гостевого однослотового подцикла» (баг №37:
«Такер Уэйн» — вся тетралогия на позиции 8 «Отряда «Сигма»») пропускал
записи без номера и видел у подсерии одну позицию {1} — сливал её с
родителем: «На полную мощность» сталкивалась с «1. Царь зверей» и уходила
в исключённые, сборник рассказов вставал перед томом 1.

Гость — только подсерия, у ВСЕХ книг которой один номер, и только в
свободный слот родителя.
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Антон</first-name><last-name>Емельянов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("царь зверь мир сила волк лес охота стая клык город арена бой "
          "зверолов питомец уровень навык рассказ мощность").split()
AUTHOR = "Емельянов Антон, Савинов Сергей"
ROOT = "Царь зверей"


def _text(seed: int) -> str:
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))


def _book(root: Path, folder: str, series: str, name: str, n: str, title: str, seed: int,
          meta_series: str = "") -> BookRecord:
    (root / folder).mkdir(parents=True, exist_ok=True)
    (root / folder / name).write_text(_FB2.format(title=title, text=_text(seed)), encoding="utf-8")
    return BookRecord(
        file_path=f"{folder}/{name}", file_title=title,
        metadata_authors="Антон Емельянов, Сергей Савинов", proposed_author=AUTHOR,
        author_source="folder_dataset", metadata_series=meta_series,
        proposed_series=series, series_source="folder_dataset",
        series_number=n, series_number_source="filename_prefix" if n else "",
    )


def _parent(root):
    return [_book(root, ROOT, ROOT, f"{n}. Царь зверей {n}.fb2", str(n), f"Царь зверей {n}", n, ROOT)
            for n in (1, 2, 3)]


def _groups(root, recs):
    return [g for g in FB2CompilerService().find_groups(recs, root) if g.author == AUTHOR]


def test_spinoff_subfolder_is_not_merged_into_parent(tmp_path):
    sub = f"{ROOT}\\Мир Царя зверей"
    spinoff = [
        _book(tmp_path, f"{ROOT}/Мир Царя зверей", sub,
              "1. На полную мощность (сюжет после 3 части Царя зверей).fb2", "1", "На полную мощность", 11),
        _book(tmp_path, f"{ROOT}/Мир Царя зверей", sub,
              "2. Сборник рассказов по мирам Емельянова и Савинова.fb2", "", "Сборник рассказов", 12),
    ]
    groups = _groups(tmp_path, _parent(tmp_path) + spinoff)

    parent = next(g for g in groups if g.series == ROOT)
    assert [b.abs_path.name for b in parent.books] == [
        "1. Царь зверей 1.fb2", "2. Царь зверей 2.fb2", "3. Царь зверей 3.fb2"]
    lost = {Path(p).name for g in groups for p in g.duplicate_paths + g.excluded_paths}
    assert not lost


def test_guest_subcycle_in_a_free_slot_still_merges(tmp_path):
    """Баг №37 по-прежнему: однослотовый подцикл в свободной позиции родителя
    («Такер Уэйн» на позиции 8) — внутри сборника родителя."""
    sub = f"{ROOT}\\Гость"
    guest = [_book(tmp_path, f"{ROOT}/Гость", sub, f"4. Гость {m}.fb2", "4", f"Гость {m}", 40 + m)
             for m in (1, 2)]
    groups = _groups(tmp_path, _parent(tmp_path) + guest)

    assert len(groups) == 1
    assert sorted(b.abs_path.name for b in groups[0].books) == sorted(
        ["1. Царь зверей 1.fb2", "2. Царь зверей 2.fb2", "3. Царь зверей 3.fb2",
         "4. Гость 1.fb2", "4. Гость 2.fb2"])
