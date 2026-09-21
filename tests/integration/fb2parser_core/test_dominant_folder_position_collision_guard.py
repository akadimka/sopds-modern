"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-
roadmap.md, баг №109.

Реальный случай (замечен пользователем на живой библиотеке): "Михайлов
Руслан - Сборник\\Мир Вальдиры" — организационная папка автора,
содержащая НЕСКОЛЬКИХ независимых серий (каждая со своей нумерацией
книг): "Герой крайних рубежей" (1-9), "Кроу", "Господство клана
Неспящих", "Сточные Воды Альгоры", "Рассказы о Вальдире", "Цикл Люца".
`_postcheck_build_subfolder_hierarchy()` присваивает им общий корень
"Мир Вальдиры\\<Подсерия>" по совпадению имени папки — без проверки,
что их собственная нумерация не пересекается с соседями по тому же
корню.

"Фильтр 1.5" (приоритет доминирующей папки, `find_groups()`) считал
самую крупную подсерию (здесь — "Герой крайних рубежей", 9 позиций)
доминирующей и удалял ЛЮБУЮ книгу из другой папки с совпавшим номером
позиции — включая "Мир Вальдиры\\Цикл Люца\\1. Маньяк отмели, или
Песочница для короля.fb2" (позиция 1, совершенно другая книга другого
цикла), которая совпадала по номеру только случайно. Файл попадал в
`duplicate_paths` — кандидат на автоматическое удаление при
синхронизации, хотя дублем не был.

Фикс: перед тем как считать совпадение позиции дублем, "Фильтр 1.5"
теперь требует хотя бы одно общее значимое слово (≥4 символа, без
ведущего числового префикса) между stem кандидата и stem книги(-книг)
доминирующей папки на той же позиции. Нет общих слов — не дубль,
оставляем обе книги (пусть и с совпавшей позицией — это честнее, чем
молча удалить не-дубль).
"""
from pathlib import Path

import pytest

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService

_FB2_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Руслан</first-name><last-name>Михайлов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body></FictionBook>
"""


def _write_book(dir_: Path, filename: str, title: str, unique_text: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / filename
    p.write_text(_FB2_TMPL.format(title=title, text=unique_text * 20), encoding="utf-8")
    return p


def _rec(file_path, title, series, series_number, unique_text,
         author="Михайлов Руслан", metadata_authors="Руслан Михайлов"):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors=metadata_authors, proposed_author=author,
        author_source="folder_dataset", metadata_series=series, proposed_series=series,
        series_source="folder_dataset+subfolder_hierarchy", series_number=series_number,
        series_number_source="filename_prefix" if series_number else "",
    )


@pytest.fixture
def records(tmp_path):
    recs = []
    gkr_dir = tmp_path / "Мир Вальдиры" / "Герой крайних рубежей"
    gkr_titles = {
        1: "Герой озёрного края", 2: "Тернистый путь вниз", 3: "Второй великий катаклизм",
        4: "Адское веселье", 5: "Аньгора", 6: "Аньгора. Часть вторая",
        7: "Братство тропы", 8: "Грань Забытых Земель", 9: "Сердце Забытых Земель",
    }
    for n, title in gkr_titles.items():
        fname = f"ГКР-{n}. {title}.fb2"
        _write_book(gkr_dir, fname, title, f"Уникальный текст ГКР {n} про {title}.")
        recs.append(_rec(
            f"Мир Вальдиры\\Герой крайних рубежей\\{fname}", title,
            "Мир Вальдиры\\Герой крайних рубежей", str(n),
            f"Уникальный текст ГКР {n} про {title}.",
        ))

    lyuts_dir = tmp_path / "Мир Вальдиры" / "Цикл Люца"
    fname = "1. Маньяк отмели, или Песочница для короля.fb2"
    _write_book(lyuts_dir, fname, "Хроники Люцериуса Великолепного",
                "Совсем другая история про Люцериуса, никак не связанная с ГКР.")
    recs.append(_rec(
        f"Мир Вальдиры\\Цикл Люца\\{fname}", "Хроники Люцериуса Великолепного",
        "Мир Вальдиры\\Цикл Люца", "1",
        "Совсем другая история про Люцериуса, никак не связанная с ГКР.",
    ))
    return recs


class TestUnrelatedSubseriesNotDeletedAsDuplicate:
    def test_unrelated_book_survives_and_is_not_marked_duplicate(self, records, tmp_path):
        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)

        matches = [g for g in groups if g.author == "Михайлов Руслан"]
        assert len(matches) == 1, [g.series for g in matches]
        group = matches[0]

        dup_names = {p.name for p in (group.duplicate_paths or [])}
        assert "1. Маньяк отмели, или Песочница для короля.fb2" not in dup_names

        book_names = {b.abs_path.name for b in group.books}
        assert "1. Маньяк отмели, или Песочница для короля.fb2" in book_names

    def test_all_nine_gkr_books_still_present(self, records, tmp_path):
        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        group = next(g for g in groups if g.author == "Михайлов Руслан")

        gkr_books = [b for b in group.books if b.abs_path.name.startswith("ГКР-")]
        assert len(gkr_books) == 9


class TestDominantFolderStillDropsGenuineDuplicate:
    """Sanity: настоящий дубль (то же название, просто не в той папке)
    по-прежнему распознаётся и удаляется — фикс не сломал исходное
    назначение "Фильтра 1.5"."""

    def test_stray_duplicate_in_root_still_removed(self, tmp_path):
        series_dir = tmp_path / "Автор Тест" / "Серия"
        titles = {1: "Пробуждение", 2: "Затмение", 3: "Возрождение", 4: "Отражение", 5: "Искупление"}
        recs = []
        for n, title in titles.items():
            fname = f"{n}. {title}.fb2"
            _write_book(series_dir, fname, title, f"Уникальный текст тома {n}.")
            recs.append(_rec(
                f"Автор Тест\\Серия\\{fname}", title, "Серия", str(n),
                f"Уникальный текст тома {n}.", author="Автор Тест", metadata_authors="Тест Автор",
            ))

        # Тот же том 3 ("Возрождение"), случайно оставленный прямо в папке
        # автора — ОДИНАКОВОЕ название, значит должен распознаться как дубль.
        stray_fname = "Серия 3. Возрождение.fb2"
        _write_book(tmp_path / "Автор Тест", stray_fname, "Возрождение", "Уникальный текст тома 3.")
        recs.append(_rec(
            f"Автор Тест\\{stray_fname}", "Возрождение", "Серия", "3",
            "Уникальный текст тома 3.", author="Автор Тест", metadata_authors="Тест Автор",
        ))

        svc = FB2CompilerService()
        groups = svc.find_groups(recs, tmp_path)
        group = next(g for g in groups if g.series == "Серия")

        dup_names = {p.name for p in (group.duplicate_paths or [])}
        assert stray_fname in dup_names
        assert len(group.books) == 5
