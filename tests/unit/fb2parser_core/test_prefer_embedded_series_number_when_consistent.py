"""Регрессия для `RegenCSVService._postcheck_prefer_embedded_series_number_when_consistent()`.

Реальный случай (Криптонов Василий / "Место силы"): 4 файла с ведущей
позицией файла В ПАПКЕ ("1.", "2.", "3.", "4." — `filename_prefix`), но
настоящие номера томов встроены в само имя файла сразу после названия
серии: "1. Место силы 1-2.fb2", "2. Место силы 3. Тьма внутри.fb2",
"3. Место силы 4. Андромеда.fb2", "4. Место силы 5. ...fb2" — реальных
томов пять (1-2, 3, 4, 5), а не четыре (1, 2, 3, 4). Правило
"filename_prefix" в _correct_series_number_from_filename забирало ведущее
число первым, вообще не добираясь до встроенного диапазона — компиляция
не находила серию как единое целое.

Контрпример безопасности (Вязовский Алексей / "Режим бога",
docs/quality-roadmap.md): "04. Вязовский - Режим бога 1. ...fb2" тоже даёт
встроенный номер "1" рядом с именем серии, отличный от ведущей позиции
"04" — но в ТОЙ ЖЕ папке есть ещё 3 файла того же автора/серии под другим
псевдонимом ("01-03. С.К.С. - Режим бога. Книга N.fb2"), у которых
встроенного матча "SeriesRoot N" вообще нет (только "Книга N"). Раз матч
есть НЕ У ВСЕХ записей группы — постчек не срабатывает, ведущая позиция
в папке (1..12) остаётся авторитетной.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(file_path, series_number, title, author="Криптонов Василий", series="Место силы"):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors=author, proposed_author=author, author_source="folder_dataset",
        metadata_series=series, proposed_series=series, series_source="folder_dataset",
        series_number=series_number, series_number_source="filename_prefix",
    )


def _run(records):
    service = RegenCSVService(_config_path())
    service.records = records
    service._postcheck_prefer_embedded_series_number_when_consistent()
    return records


class TestConsistentEmbeddedRangeOverridesFolderPosition:
    def test_all_four_files_get_true_embedded_numbers(self):
        records = [
            _rec("1. Место силы 1-2.fb2", "1", "Место Силы 1-2"),
            _rec("2. Место силы 3. Тьма внутри.fb2", "2", "Место Силы 3. Тьма внутри"),
            _rec("3. Место силы 4. Андромеда.fb2", "3", "Андромеда"),
            _rec("4. Место силы 5. Трагикомедия в пяти актах.fb2", "4", "Трагикомедия в пяти актах"),
        ]
        _run(records)
        assert [r.series_number for r in records] == ["1-2", "3", "4", "5"]
        assert all(r.series_number_source == "filename_series_root_consistent" for r in records)


class TestGapOrCollisionLeavesFolderPositionUntouched:
    def test_pseudonym_change_without_full_group_match_not_touched(self):
        # Имитация "Режим бога": 3 файла БЕЗ встроенного "SeriesRoot N"
        # (только "Книга N", другой паттерн) + 1 файл С embedded-номером,
        # отличным от ведущей позиции. Матч есть не у всех — не трогаем.
        records = [
            _rec("01. С.К.С. - Режим бога. Книга 1.fb2", "1",
                 "Книга 1", author="Вязовский Алексей", series="Режим бога"),
            _rec("02. С.К.С. - Режим бога. Книга 2.fb2", "2",
                 "Книга 2", author="Вязовский Алексей", series="Режим бога"),
            _rec("03. С.К.С. - Режим бога. Книга 3.fb2", "3",
                 "Книга 3", author="Вязовский Алексей", series="Режим бога"),
            _rec("04. Вязовский - Режим бога 1. Восход Красной Звезды.fb2", "4",
                 "Восход Красной Звезды", author="Вязовский Алексей", series="Режим бога"),
        ]
        _run(records)
        assert [r.series_number for r in records] == ["1", "2", "3", "4"]
        assert all(r.series_number_source == "filename_prefix" for r in records)

    def test_gap_in_embedded_range_not_touched(self):
        # Встроенные номера есть у всех, но с дырой (1, 2, потом сразу 5) —
        # не рискуем считать это надёжной последовательностью.
        records = [
            _rec("1. Тест 1.fb2", "1", "Тест 1", series="Тест"),
            _rec("2. Тест 2.fb2", "2", "Тест 2", series="Тест"),
            _rec("3. Тест 5.fb2", "3", "Тест 5", series="Тест"),
        ]
        _run(records)
        assert [r.series_number for r in records] == ["1", "2", "3"]

    def test_overlapping_embedded_ranges_not_touched(self):
        # Диапазоны пересекаются (1-3 и 2-4) — задвоение позиции 2-3, не трогаем.
        records = [
            _rec("1. Тест 1-3.fb2", "1", "Тест 1-3", series="Тест"),
            _rec("2. Тест 2-4.fb2", "2", "Тест 2-4", series="Тест"),
        ]
        _run(records)
        assert [r.series_number for r in records] == ["1", "2"]

    def test_single_record_group_not_touched(self):
        # Группа из одной записи — сравнивать не с чем, менять нечего.
        records = [_rec("1. Тест 1.fb2", "1", "Тест 1", series="Тест")]
        _run(records)
        assert records[0].series_number == "1"
        assert records[0].series_number_source == "filename_prefix"
