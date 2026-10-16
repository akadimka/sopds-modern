"""Две редакции одной книги на одной позиции: в сборник — новая, старая
остаётся отдельным файлом (баг №156).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md), решение пользователя
2026-10-14: как у Калашникова «Между сном и явью (новая редакция)» — для
всех. Кузьмин Марк «Арессия»: «1. Новая жизнь (новая версия)» и «1. Новая
жизнь», Лукьянов Александр: «1. Гремя огнём… (иная редакция)» и «1. Гремя
огнём…» — обе редакции шли в сборник на позицию 1. А при большом общем
тексте одна из них могла уйти в дубли на удаление (бóльшая по тексту
оставалась, независимо от того, какая новее).

Пометка редакции в имени файла («новая/иная/вторая редакция», «новая
версия», «Ребилд») + то же название на той же позиции: помеченная — в
сборнике, другая — исключена (остаётся на месте, не удаляется).
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Марк</first-name><last-name>Кузьмин</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("арессия жизнь судьба путь маг меч замок король лес река дорога ночь "
          "огонь враг друг тайна знак город стража орден").split()
AUTHOR, SERIES = "Кузьмин Марк", "Арессия"


def _text(seed: int, share_with: int = None) -> str:
    rnd = random.Random(seed)
    own = " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))
    return own if share_with is None else _text(share_with) + " " + own


def _book(root: Path, name: str, n: int, title: str, text: str) -> BookRecord:
    (root / SERIES).mkdir(parents=True, exist_ok=True)
    (root / SERIES / name).write_text(_FB2.format(title=title, text=text), encoding="utf-8")
    return BookRecord(
        file_path=f"{SERIES}/{name}", file_title=title,
        metadata_authors="Марк Кузьмин", proposed_author=AUTHOR,
        author_source="folder_dataset", metadata_series=SERIES,
        proposed_series=SERIES, series_source="folder_dataset",
        series_number=str(n), series_number_source="filename_prefix",
    )


def _group(root, recs):
    groups = [g for g in FB2CompilerService().find_groups(recs, root) if g.author == AUTHOR]
    assert len(groups) == 1, [(g.series, g.cleanup_only) for g in groups]
    return groups[0]


def _rest(root):
    return [_book(root, "2. Новая судьба.fb2", 2, "Арессия – Новая судьба", _text(2)),
            _book(root, "3. Новый путь.fb2", 3, "Новый путь", _text(3))]


def test_new_version_compiled_old_kept(tmp_path):
    recs = [
        _book(tmp_path, "1. Новая жизнь (новая версия).fb2", 1,
              "Арессия - Новая жизнь (новая версия)", _text(11)),
        _book(tmp_path, "1. Новая жизнь.fb2", 1, "Новая жизнь", _text(10)),
    ] + _rest(tmp_path)
    g = _group(tmp_path, recs)
    assert [b.abs_path.name for b in g.books] == [
        "1. Новая жизнь (новая версия).fb2", "2. Новая судьба.fb2", "3. Новый путь.fb2"]
    assert [p.name for p in g.excluded_paths] == ["1. Новая жизнь.fb2"]
    assert not g.duplicate_paths


def test_old_edition_with_shared_text_is_not_deleted(tmp_path):
    """Старая редакция почти целиком в новой (бóльшая по тексту — новая):
    раньше уходила в дубли на удаление — теперь только исключается."""
    recs = [
        _book(tmp_path, "1. Новая жизнь (иная редакция).fb2", 1, "Новая жизнь", _text(11, share_with=10)),
        _book(tmp_path, "1. Новая жизнь.fb2", 1, "Новая жизнь", _text(10)),
    ] + _rest(tmp_path)
    g = _group(tmp_path, recs)
    assert g.books[0].abs_path.name == "1. Новая жизнь (иная редакция).fb2"
    assert [p.name for p in g.excluded_paths] == ["1. Новая жизнь.fb2"]
    assert not g.duplicate_paths


def test_volume_with_version_in_title_at_its_own_position_is_untouched(tmp_path):
    """«Игра богов (версия 2.0)» под своим номером — отдельная книга, не
    редакция книги на другой позиции."""
    recs = [
        _book(tmp_path, "1. Игра богов.fb2", 1, "Игра богов", _text(1)),
        _book(tmp_path, "2. Игра без правил.fb2", 2, "Игра без правил", _text(2)),
        _book(tmp_path, "3. Игра богов (версия 2.0).fb2", 3, "Игра богов (версия 2.0)", _text(3)),
    ]
    g = _group(tmp_path, recs)
    assert len(g.books) == 3 and not g.excluded_paths and not g.duplicate_paths
