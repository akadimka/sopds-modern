"""Регрессия для `RegenCSVService._postcheck_strip_service_words()` —
docs/quality-roadmap.md, баг №35.

Реальный случай: "Тимофеев Денис / Дети Пекла" — арка "Человек из Пекла"
разбита на "Книга 1" (1 файл), "Книга 2" (3 файла: "Часть 1/2/3") и
"Книга 3" (1 файл). Одиночные "Книга N" (файлы с уникальным номером)
корректно схлопывались в чистое "Человек из Пекла" — их номер стрипался
ещё в `_detect_named_arcs()`, оставляя голое "...Книга" (без числа),
которое эта функция ловила. Но "Книга 2" (устойчивый, повторяющийся
сегмент у 3 файлов) сохраняло номер — старый regex ловил только ГОЛОЕ
служебное слово, без числа после него. Серия физически расходилась на
"Человек из Пекла" и "Человек из Пекла. Книга 2" — компилятор группирует
по точной строке и видел ДВЕ разные серии вместо одной
последовательности томов 1-5.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, number):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors="Денис Тимофеев",
        proposed_author="Тимофеев Денис", author_source="filename",
        metadata_series="", proposed_series=series, series_source="filename_named_arc",
        series_number=number,
    )


class TestStripServiceWordWithTrailingNumber:
    def test_book_n_suffix_stripped_even_with_number(self):
        recs = [
            _rec("Тимофеев Денис - Дети Пекла 1. Человек из Пекла. Книга 1.fb2",
                 "Дети Пекла\\Человек из Пекла", "1"),
            _rec("Тимофеев Денис - Дети Пекла 2. Человек из Пекла. Книга 2. Часть 1.fb2",
                 "Дети Пекла\\Человек из Пекла. Книга 2", "2"),
            _rec("Тимофеев Денис - Дети Пекла 3. Человек из Пекла. Книга 2. Часть 2.fb2",
                 "Дети Пекла\\Человек из Пекла. Книга 2", "3"),
            _rec("Тимофеев Денис - Дети Пекла 5. Человек из Пекла. Книга 3.fb2",
                 "Дети Пекла\\Человек из Пекла", "5"),
        ]
        service = RegenCSVService(_config_path())
        service.records = recs
        service._postcheck_strip_service_words()

        for rec in recs:
            assert rec.proposed_series == "Дети Пекла\\Человек из Пекла", rec.file_path

    def test_meaningful_trailing_word_with_number_not_stripped(self):
        # Sanity: число после НЕ-служебного слова (не входит в список
        # книга/том/часть/book/vol/volume) по-прежнему не трогается.
        rec = _rec("Автор - Серия. Хроники 12.fb2", "Серия\\Хроники 12", "1")
        service = RegenCSVService(_config_path())
        service.records = [rec]
        service._postcheck_strip_service_words()
        assert rec.proposed_series == "Серия\\Хроники 12"
