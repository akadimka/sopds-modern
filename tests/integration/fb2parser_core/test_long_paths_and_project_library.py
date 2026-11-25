"""Найдено пользователем в `regen_Test1.csv` (2026-11-25), новая папка
«Акунин Борис - Сборник\\История\\Библиотека проекта Б. Акунина История
Российского государства\\Том I…\\Документальные дополнения\\…»:

- 23 книги из 47 — с автором «[unknown]» и названием «[no title]»: путь
  длиннее 260 символов, Windows без LongPathsEnabled не открывает файл
  (open падает, exists() — False). Решение — `fb2_utils.long_path()` во
  всех обращениях к файлам (чтение, кэш, компилятор, синхронизация).
- Книги разных авторов (Васильев, Толстой, Валишевский…) получали серию
  из папок-рубрик («Документальные дополнения», «Том I. …») или обрывок
  «Библиотека проекта Б\\Том V…». Решение пользователя — без серии:
  «Библиотека проекта» в `no_series_folder_names` (app_settings.json).
"""
import os

import pytest

from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor
from fb2parser_core.fb2_utils import long_path, read_fb2_bytes
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>prose_history</genre>
<author><first-name>Вера</first-name><last-name>Панова</last-name></author>
<book-title>{title}</book-title>{sequence}
</title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_LIBRARY = ("Акунин Борис - Сборник", "История",
            "Библиотека проекта Б. Акунина История Российского государства")
_LONG_NAME = ("IХД-03. Панова В. Сказание об Ольге (Сказание о Феодосии. "
              "Феодорец Белый Клобучок) — очень длинное имя файла для проверки.fb2")


def _write(path, title, seq=""):
    os.makedirs(long_path(str(path.parent)), exist_ok=True)
    long_path(path).write_text(_FB2.format(title=title, sequence=seq), encoding="utf-8")


def _long_book(tmp_path):
    folder = tmp_path.joinpath(*_LIBRARY, "Том I. От истоков до монгольского нашествия",
                               "Художественные дополнения")
    path = folder / _LONG_NAME
    assert len(str(path)) > 260
    _write(path, "Сказание об Ольге")
    return path


class TestLongPath:
    def test_short_path_unchanged(self, tmp_path):
        p = tmp_path / "книга.fb2"
        assert long_path(p) == p
        assert long_path(str(p)) == str(p)

    @pytest.mark.skipif(os.name != "nt", reason="ограничение MAX_PATH — только Windows")
    def test_long_path_prefixed_once(self, tmp_path):
        p = _long_book(tmp_path)
        lp = long_path(p)
        assert str(lp).startswith("\\\\?\\")
        assert long_path(lp) == lp
        assert lp.exists()

    def test_book_with_long_path_is_read(self, tmp_path):
        p = _long_book(tmp_path)
        assert b"<book-title>" in read_fb2_bytes(p)
        meta = FB2SAXExtractor()._extract_all_metadata_at_once(p)
        assert meta["title"] == "Сказание об Ольге"
        assert meta["authors"] == "Вера Панова"

    def test_regen_reads_long_path_book(self, tmp_path):
        _long_book(tmp_path)
        [rec] = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
        assert rec.file_title == "Сказание об Ольге"
        assert rec.metadata_authors == "Вера Панова"


class TestProjectLibraryHasNoSeries:
    def test_in_shared_no_series_names(self):
        service = RegenCSVService(_config_path())
        assert "библиотека проекта" in service.settings.get_no_series_folder_names()

    def test_postcheck_clears_rubric_and_arc_series(self):
        service = RegenCSVService(_config_path())
        base = "\\".join(_LIBRARY)
        rubric = BookRecord(
            file_path=f"{base}\\Том IV. Семнадцатый век\\Документальные дополнения\\IVДД-02. Валишевский К. Смутное время.fb2",
            file_title="Смутное время", metadata_authors="Казимир Валишевский",
            proposed_author="Валишевский Казимир", author_source="metadata",
            metadata_series="", proposed_series="Документальные дополнения", series_source="folder_dataset")
        arc = BookRecord(
            file_path=f"{base}\\Том V. Царь Пётр Алексеевич\\Художественные дополнения\\VХД-01. Соловьев В. Царь-девица.fb2",
            file_title="Царь-девица", metadata_authors="Всеволод Соловьев",
            proposed_author="Соловьев Всеволод", author_source="metadata",
            metadata_series="", proposed_series="Библиотека проекта Б\\Том V. Царь Петр Алексеевич",
            series_source="filename_named_arc")
        service.records = [rubric, arc]
        service._no_series_names = service.settings.get_no_series_folder_names()
        service._postcheck_no_series_marker()
        assert (rubric.proposed_series, arc.proposed_series) == ("", "")

    def test_regen_folder_books_without_series(self, tmp_path):
        base = tmp_path.joinpath(*_LIBRARY)
        seq = '<sequence name="Библиотека проекта Б. Акунина «История Российского государства»"/>'
        for vol, rubric, name, title in (
            ("Том I. От истоков", "Художественные дополнения", "IХД-05. Загребельный П. Ярослав Мудрый.fb2", "Ярослав Мудрый"),
            ("Том I. От истоков", "Художественные дополнения", "IХД-06. Ладинский А. Голубь над Понтом.fb2", "Голубь над Понтом"),
            ("Том IV. Семнадцатый век", "Документальные дополнения", "IVДД-02. Валишевский К. Смутное время.fb2", "Смутное время"),
            ("Том IV. Семнадцатый век", "Документальные дополнения", "IVДД-03. Валишевский К. Образование Государства.fb2", "Образование государства"),
        ):
            _write(base / vol / rubric / name, title, seq)
        records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
        assert len(records) == 4
        assert [r.proposed_series for r in records] == [""] * 4
