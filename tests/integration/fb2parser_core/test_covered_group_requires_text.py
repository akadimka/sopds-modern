"""Подавление «покрытой» группы — только по тексту (баг №151).

`FB2CompilerService._suppress_covered_groups` (шаг 4 find_groups) объявляет
группу автора покрытой другой его группой и отдаёт ВСЕ её книги в дубли на
удаление (kept_paths=[]) — по совпадению content_hash (SHA-256 первых
256 КБ) или по совпадению названий у 75% книг. Этот шаг идёт после
страховки №141 (`_verify_duplicate_paths` в `_emit`), так что текст книг
никто не сверял: при совпадении 3 названий из 4 четвёртая — уникальная —
книга удалялась. Найдено при разборе восстановительной компиляции после
бага №150 (сервер, 2026-10-13).

Теперь группа подавляется, только если текст каждой её книги есть в книгах
покрывающей группы (тот же порог, что и в №141); иначе она остаётся
обычной группой компиляции.
"""
import random
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Олег</first-name><last-name>Данильченко</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>{text}</p></section></body></FictionBook>
"""
_VOCAB = ("звезда корабль вояж империя флот капитан порт война дорога ночь огонь "
          "станция планета совет дом поле лес река враг друг тайна путь знак").split()
_TITLES = ["Хроники. Первая книга", "Хроники. Вторая книга", "Хроники. Третья книга",
           "Хроники. Четвертая книга", "Хроники. Пятая книга"]


def _text(seed: int) -> str:
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(_VOCAB) for _ in range(12)) + "." for _ in range(300))


def _book(root: Path, folder: str, series: str, n: int, title: str, text: str) -> BookRecord:
    rel = f"{folder}/{n}. {title}.fb2"
    (root / folder).mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(_FB2.format(title=title, text=text), encoding="utf-8")
    return BookRecord(
        file_path=rel, file_title=title,
        metadata_authors="Олег Данильченко", proposed_author="Данильченко Олег",
        author_source="folder_dataset", metadata_series=series,
        proposed_series=series, series_source="folder_dataset",
        series_number=str(n), series_number_source="filename_prefix",
    )


def _large(root):
    """«Мир\\Хроники», тома 1–4 — группа с «правильной» серией."""
    return [_book(root, "Мир/Хроники", "Мир\\Хроники", n, _TITLES[n - 1], _text(n)) for n in (1, 2, 3, 4)]


def _groups(root, recs):
    return {g.series: g for g in FB2CompilerService().find_groups(recs, root)}


def test_unique_book_of_title_covered_group_is_not_deleted(tmp_path):
    # «Хроники»: тома 1–3 — копии томов «Мир\Хроники», том 4 — другая книга
    # (своё название и текст). Совпадение названий 3/4 = 75%.
    small = [_book(tmp_path, "Хроники", "Хроники", n, _TITLES[n - 1], _text(n)) for n in (1, 2, 3)]
    small.append(_book(tmp_path, "Хроники", "Хроники", 4, _TITLES[4], _text(5)))

    groups = _groups(tmp_path, _large(tmp_path) + small)

    deleted = {p.name for g in groups.values() for p in g.duplicate_paths}
    assert "4. Хроники. Пятая книга.fb2" not in deleted
    compiled = {b.abs_path.name for g in groups.values() for b in g.books}
    assert "4. Хроники. Пятая книга.fb2" in compiled


def test_group_whose_books_are_all_copies_is_still_suppressed(tmp_path):
    small = [_book(tmp_path, "Хроники", "Хроники", n, _TITLES[n - 1], _text(n)) for n in (1, 2, 3, 4)]

    groups = _groups(tmp_path, _large(tmp_path) + small)

    cleanup = [g for g in groups.values() if g.cleanup_only]
    assert len(cleanup) == 1
    assert sorted(p.name for p in cleanup[0].duplicate_paths) == sorted(
        f"{n}. {_TITLES[n - 1]}.fb2" for n in (1, 2, 3, 4))
    assert [g for g in groups.values() if not g.cleanup_only][0].series == "Мир\\Хроники"


def test_same_titles_but_different_text_is_not_suppressed(tmp_path):
    """Те же названия, другой текст (другие книги или другая редакция) —
    раньше вся группа уходила в дубли по одним названиям."""
    small = [_book(tmp_path, "Хроники", "Хроники", n, _TITLES[n - 1], _text(100 + n)) for n in (1, 2, 3, 4)]

    groups = _groups(tmp_path, _large(tmp_path) + small)

    assert not any(g.cleanup_only for g in groups.values())
    assert not any(g.duplicate_paths for g in groups.values())
