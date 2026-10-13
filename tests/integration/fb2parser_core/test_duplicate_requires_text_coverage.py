"""Страховка `FB2CompilerService._verify_duplicate_paths` — docs/quality-
roadmap.md, баг №141.

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md): из 47 файлов, помеченных
компилятором как дубли на удаление, настоящих оказалось 4 — текст остальных
43 с остающимися файлами группы совпадал на 0–2%. Реальный случай (Вяч
Павел / «Претендент»): «1. Сила рода. Том 1. Том 2.fb2» распознавался как
тома 1-2, а «2. Сила рода. Том 3.fb2» по префиксу «2.» считался покрытым
им и уходил в дубли.

Фикс: файл удаляется как дубль, только если его текст действительно есть в
остающихся файлах группы; иначе он исключается из компиляции, но остаётся
на месте и его можно вернуть.
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Павел</first-name><last-name>Вяч</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("род сила маг клинок замок князь граница война дорога ночь огонь "
          "кровь меч совет дом поле лес река враг друг тайна путь знак").split()


def _text(seed: int) -> str:
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))


def _book(dir_: Path, name: str, title: str, number: str, text: str) -> BookRecord:
    (dir_ / name).write_text(_FB2.format(title=title, text=text), encoding="utf-8")
    return BookRecord(
        file_path=name, file_title=title,
        metadata_authors="Павел Вяч", proposed_author="Вяч Павел",
        author_source="folder_dataset", metadata_series="Претендент",
        proposed_series="Претендент", series_source="folder_dataset",
        series_number=number, series_number_source="filename_prefix",
    )


def _group(tmp_path: Path, recs: list):
    groups = [g for g in FB2CompilerService().find_groups(recs, tmp_path)
              if g.author == "Вяч Павел"]
    assert len(groups) == 1, [(g.series, g.cleanup_only) for g in groups]
    return groups[0]


def test_unique_volume_is_not_deleted(tmp_path):
    recs = [
        _book(tmp_path, "1. Сила рода. Том 1. Том 2.fb2", "Сила рода. Том 1 и Том 2", "1-2",
              _text(1) + " " + _text(2)),
        _book(tmp_path, "2. Сила рода. Том 3.fb2", "Сила рода. Том 3", "2", _text(3)),
        _book(tmp_path, "3. Сила рода. Том 4.fb2", "Сила рода. Том 4", "3", _text(4)),
    ]
    group = _group(tmp_path, recs)
    deleted = {p.name for p in group.duplicate_paths}
    assert "2. Сила рода. Том 3.fb2" not in deleted
    kept = {b.abs_path.name for b in group.books} | {p.name for p in group.excluded_paths}
    assert "2. Сила рода. Том 3.fb2" in kept


def test_real_copy_is_still_deleted(tmp_path):
    """Копия, текст которой целиком есть в другом файле, по-прежнему дубль."""
    recs = [
        _book(tmp_path, "1. Сила рода. Том 1. Том 2.fb2", "Сила рода. Том 1 и Том 2", "1-2",
              _text(1) + " " + _text(2)),
        _book(tmp_path, "1. Сила рода. Том 1.fb2", "Сила рода. Том 1", "1", _text(1)),
        _book(tmp_path, "3. Сила рода. Том 3.fb2", "Сила рода. Том 3", "3", _text(3)),
    ]
    group = _group(tmp_path, recs)
    assert {p.name for p in group.duplicate_paths} == {"1. Сила рода. Том 1.fb2"}
