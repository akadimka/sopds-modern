"""Части одного тома под одним номером — все в компиляции, по порядку частей
(баг №153).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md):
- Коршунов Павел «Мир Нории. Жестокая игра»: «5. Древние боги. Том 1/2/3»
  — три книги на позиции 5; «Том 2» уходил в дубли (после №141 — в
  исключённые), Том 1 и Том 3 стояли оба как «5»;
- Ральт Владимир «Монстры этого мира»: «3. … Книга 2. Конвейер душ. Часть
  1/2» — «Часть 1» выпадала.

Части одного тома — разные тексты (не копии): каждая — в сборнике, на
позиции N.1, N.2, … по номеру части.
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Павел</first-name><last-name>Коршунов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("нория игра бог древний мир смерть жизнь рождение тень война монстр душа "
          "конвейер надежда падший город клан маг меч рейд").split()


def _text(seed: int) -> str:
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))


def _book(root: Path, author: str, series: str, name: str, n: int, title: str, seed: int,
          meta_series: str = None) -> BookRecord:
    (root / series).mkdir(parents=True, exist_ok=True)
    (root / series / name).write_text(_FB2.format(title=title, text=_text(seed)), encoding="utf-8")
    return BookRecord(
        file_path=f"{series}/{name}", file_title=title,
        metadata_authors=author, proposed_author=author,
        author_source="folder_dataset", metadata_series=series if meta_series is None else meta_series,
        proposed_series=series, series_source="folder_dataset",
        series_number=str(n), series_number_source="filename_prefix",
    )


def _group(root, recs, author):
    groups = [g for g in FB2CompilerService().find_groups(recs, root) if g.author == author]
    assert len(groups) == 1, [(g.series, g.cleanup_only) for g in groups]
    return groups[0]


def test_korshunov_three_volumes_of_book_five(tmp_path):
    a, s = "Коршунов Павел", "Мир Нории. Жестокая игра"
    # метаданные — как в библиотеке: «Мир Нории», у томов 1 и 4 серии нет
    recs = [
        _book(tmp_path, a, s, "1. Рождение.fb2", 1, "Жестокая игра. Рождение", 1, meta_series=""),
        _book(tmp_path, a, s, "2. Жизнь.fb2", 2, "Жестокая игра. Жизнь", 2, meta_series="Мир Нории"),
        _book(tmp_path, a, s, "3. Смерть.fb2", 3, "Жестокая игра. Смерть", 3, meta_series="Мир Нории"),
        _book(tmp_path, a, s, "4. Тень войны.fb2", 4, "Жестокая игра. Книга 4. Тень войны", 4, meta_series=""),
        _book(tmp_path, a, s, "5. Древние боги. Том 1.fb2", 5,
              "Жестокая игра (книга 5) Древние боги (том 1)", 51, meta_series="Мир Нории"),
        _book(tmp_path, a, s, "5. Древние боги. Том 2.fb2", 5,
              "Жестокая игра. Книга 5. Древние боги. Том 2", 52, meta_series="Мир Нории"),
        _book(tmp_path, a, s, "5. Древние боги. Том 3.fb2", 5,
              "Жестокая игра. Книга 5. Древние боги. Том 3", 53, meta_series="Мир Нории"),
        _book(tmp_path, a, s, "6. Два мира.fb2", 6, "Жестокая игра (книга 6) Два мира", 6, meta_series="Мир Нории"),
    ]
    g = _group(tmp_path, recs, a)
    assert not g.duplicate_paths and not g.excluded_paths
    assert [b.abs_path.name for b in g.books] == [
        "1. Рождение.fb2", "2. Жизнь.fb2", "3. Смерть.fb2", "4. Тень войны.fb2",
        "5. Древние боги. Том 1.fb2", "5. Древние боги. Том 2.fb2", "5. Древние боги. Том 3.fb2",
        "6. Два мира.fb2"]
    assert [b.volume_label for b in g.books] == ["1", "2", "3", "4", "5.1", "5.2", "5.3", "6"]


def test_ralt_two_parts_of_book_two(tmp_path):
    a, s = "Ральт Владимир", "Монстры этого мира"
    recs = [
        _book(tmp_path, a, s, "1. Монстры этого мира. Книга 1.fb2", 1, "Монстры этого мира. Книга 1", 1),
        _book(tmp_path, a, s, "2. Монстры этого мира. Книга 1. Часть 2.fb2", 2,
              "Монстры этого мира. Книга 1 (Часть 2)", 2),
        _book(tmp_path, a, s, "3. Монстры этого мира. Книга 2. Конвейер душ. Часть 1.fb2", 3,
              "Монстры этого мира. Книга 2. Конвейер душ (Часть 1)", 31),
        _book(tmp_path, a, s, "3. Монстры этого мира. Книга 2. Конвейер душ. Часть 2.fb2", 3,
              "Монстры этого мира. Книга 2. Конвейер душ (Часть 2)", 32),
        _book(tmp_path, a, s, "4. Монстры этого мира. Надежда падших. Мидквел.fb2", 4,
              "Монстры этого мира. Надежда падших (Мидквел)", 4),
    ]
    g = _group(tmp_path, recs, a)
    assert not g.duplicate_paths and not g.excluded_paths
    assert [b.abs_path.name for b in g.books] == [
        "1. Монстры этого мира. Книга 1.fb2",
        "2. Монстры этого мира. Книга 1. Часть 2.fb2",
        "3. Монстры этого мира. Книга 2. Конвейер душ. Часть 1.fb2",
        "3. Монстры этого мира. Книга 2. Конвейер душ. Часть 2.fb2",
        "4. Монстры этого мира. Надежда падших. Мидквел.fb2"]
    assert [b.volume_label for b in g.books] == ["1", "2", "3.1", "3.2", "4"]


def test_two_volumes_in_one_file_are_not_parts(tmp_path):
    """«01. … Том 1. Том 2» — два тома в одном файле (повтор одного слова), не
    «часть 2 тома 1»: метка — по-прежнему позиция файла, без «1.2»."""
    a, s = "Ютин Макар", "Tower Defense"
    recs = [
        _book(tmp_path, a, s, "01. Оборона дурацкого замка. Том 1. Том 2.fb2", 1,
              "Оборона дурацкого замка. Том 1. Том 2", 1),
        _book(tmp_path, a, s, "02. Оборона дурацкого замка. Том 3.fb2", 2,
              "Оборона дурацкого замка. Том 3", 3),
        _book(tmp_path, a, s, "03. Оборона дурацкого замка. Том 4.fb2", 3,
              "Оборона дурацкого замка. Том 4", 4),
    ]
    g = _group(tmp_path, recs, a)
    assert g.books[0].abs_path.name == "01. Оборона дурацкого замка. Том 1. Том 2.fb2"
    assert g.books[0].volume_label != "1.2"
