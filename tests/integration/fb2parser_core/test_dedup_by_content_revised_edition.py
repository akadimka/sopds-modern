"""Регрессия для `FB2CompilerService._dedup_by_content` — docs/quality-
roadmap.md, баг №138.

Реальный случай («Серия - «Кинороман»», Звягинцев / «Сармат»): два издания
цикла в одной папке. В 2010 году книги переименовали: «Кофе на крови» (т. 1)
вышла как «Группа первая, резус положительный», «Любовник войны» (т. 2) — как
«Стабильное неравновесие». Второй роман переиздан без правок и отсеивался
как дубль по совпадению начала текста, а первый — в новой редакции:
начало переписано, сравнение первых 2000 символов его не ловило, и в
итоговой компиляции оказывались оба тома 1.

Фикс: для книг на одной позиции серии, если начала не совпали, сверяется
текст целиком — доля общих 6-словных фрагментов (у редакций одной книги
~50%, у разных книг ~0%).
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Александр</first-name><last-name>Звягинцев</last-name></author>
<book-title>{title}</book-title><sequence name="Сармат" number="{n}"/></title-info></description>
<body><section>{paras}</section></body></FictionBook>
"""

_VOCAB = ("генерал склонился над картой майор кишлак ущелье хребет вертолёт "
          "приказ разведка граница ночь огонь колонна связь полковник выстрел "
          "дорога туман высота группа резус кровь кофе война ветер камень").split()


def _sentences(seed: int, count: int) -> list:
    rnd = random.Random(seed)
    return [" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(count)]


def _write(dir_: Path, name: str, title: str, n: int, sentences: list) -> None:
    paras = "".join(f"<p>{s}</p>" for s in sentences)
    (dir_ / name).write_text(_FB2.format(title=title, n=n, paras=paras), encoding="utf-8")


def _rec(name: str, title: str, n: int) -> BookRecord:
    return BookRecord(
        file_path=name, file_title=title,
        metadata_authors="Александр Звягинцев", proposed_author="Звягинцев Александр",
        author_source="filename", metadata_series="Сармат", proposed_series="Сармат",
        series_source="filename+meta_confirmed", series_number=str(n),
        series_number_source="metadata",
    )


def _groups(tmp_path: Path, books: list):
    for name, title, n, sentences in books:
        _write(tmp_path, name, title, n, sentences)
    recs = [_rec(name, title, n) for name, title, n, _ in books]
    return [g for g in FB2CompilerService().find_groups(recs, tmp_path)
            if g.author == "Звягинцев Александр"]


def test_revised_edition_on_same_position_is_a_duplicate(tmp_path):
    original = _sentences(1, 400)
    # переиздание: начало переписано, каждое третье предложение заменено
    # (у реальной пары общих 6-словных фрагментов ~51%)
    revised = _sentences(99, 40) + [s if i % 3 else t for i, (s, t) in
                                    enumerate(zip(original[40:], _sentences(7, 360)))]
    groups = _groups(tmp_path, [
        ("Сармат. Кофе на крови.fb2", "Сармат. Кофе на крови", 1, original),
        ("Сармат. Группа первая, резус положительный.fb2",
         "Группа первая, резус положительный", 1, revised),
        ("Сармат. Любовник войны.fb2", "Сармат. Любовник войны", 2, _sentences(2, 400)),
        ("Сармат. Смерть поправший.fb2", "Сармат. Смерть поправший", 3, _sentences(3, 400)),
    ])
    assert len(groups) == 1
    group = groups[0]
    assert [b.volume_label for b in group.books] == ["1", "2", "3"]
    deleted = [p.name for p in group.duplicate_paths]
    assert len(deleted) == 1
    assert deleted[0] in ("Сармат. Кофе на крови.fb2", "Сармат. Группа первая, резус положительный.fb2")


def test_different_books_on_same_position_are_kept(tmp_path):
    """Две разные книги с одним номером (ошибка нумерации) — не дубли:
    общих фрагментов текста у них нет, обе остаются."""
    groups = _groups(tmp_path, [
        ("Сармат. Кофе на крови.fb2", "Сармат. Кофе на крови", 1, _sentences(1, 400)),
        ("Сармат. Другая книга.fb2", "Сармат. Другая книга", 1, _sentences(5, 400)),
        ("Сармат. Любовник войны.fb2", "Сармат. Любовник войны", 2, _sentences(2, 400)),
    ])
    deleted = [p.name for g in groups for p in (g.duplicate_paths or [])]
    assert deleted == []
