"""Тесты для `fb2parser_core.evidence` — единого реестра источников
данных и приоритета между ними (docs/quality-roadmap.md, баг №109,
раздел "размышление о хрупкости").

Модуль создан, чтобы устранить конкретно обнаруженное дублирование:
`folder_has_signal()` раньше была независимо реализована три раза
(`regen_csv.py`, дважды в `pass2_series_filename.py`) с идентичной
логикой, а приоритет "Папка > Файл > Мета" существовал только как
неформальный комментарий в нескольких местах, без единой функции.
"""
from pathlib import Path

import pytest

from fb2parser_core import evidence
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


class TestSourceTier:
    @pytest.mark.parametrize("source, expected_tier", [
        ("folder_dataset", 3),
        ("folder_hierarchy", 3),
        ("folder_meta_consensus", 3),
        ("folder_metadata_confirmed", 3),
        ("no_series_folder", 3),
        ("filename", 2),
        ("filename+meta_expanded", 2),
        ("filename_series_root", 2),
        ("filename_named_arc", 2),
        ("metadata", 1),
        ("metadata+author_expanded", 1),
        ("consensus", 1),
        ("consensus_inferred_first_volume", 1),
        ("", 0),
        (None, 0),
        ("unknown_made_up_source", 0),
    ])
    def test_tier_matches_expected(self, source, expected_tier):
        assert evidence.source_tier(source) == expected_tier

    def test_folder_multiauthor_deliberately_not_tier_3(self):
        # "folder_multiauthor" — author_source из отдельного, не
        # связанного с этим модулем механизма (не входил ни в один из
        # трёх дублированных FOLDER_SOURCES) — намеренно НЕ считается
        # папочным сигналом здесь, чтобы не менять поведение при
        # переводе существующего кода на этот модуль.
        assert evidence.source_tier("folder_multiauthor") == 0

    def test_composite_suffix_does_not_change_base_tier(self):
        # PASS4 дописывает "+series-consensus"/"+metadata-coauthors" и
        # т.п. к УЖЕ существующему источнику — суффикс не должен менять
        # тир базового значения.
        assert evidence.source_tier("folder_dataset+series-consensus") == \
            evidence.source_tier("folder_dataset")
        assert evidence.source_tier("filename+meta_expanded") == \
            evidence.source_tier("filename")


class TestSeriesSourceRank:
    """Гранулярная шкала для series_source (docs/quality-roadmap.md,
    баг №109, "хрупкость каскада" часть 4) — заменяет локальный
    _SRC_PRIORITY словарь pass3_series_normalize.py, который сравнивал
    series_source ТОЧНЫМ совпадением с литералом 'filename' и молча
    ронял приоритет всех реальных 'filename_*' значений до 0."""

    @pytest.mark.parametrize("source, expected_rank", [
        ("folder_dataset", 33),
        ("folder_hierarchy", 32),
        ("no_series_folder", 32),
        ("folder_meta_consensus", 31),
        ("folder_metadata_confirmed", 30),
        ("filename_prefix", 21),
        ("filename_prefix_pattern", 21),
        ("filename", 20),
        ("filename_named_arc", 20),
        ("filename_series_root_book1", 20),
        ("filename_phrase_confirmed", 20),
        ("filename+meta_confirmed", 20),
        ("filename_abbrev_prefix", 20),  # инфикс, а не префикс "filename_prefix"
        ("metadata", 10),
        ("consensus", 10),
        ("metadata+author_expanded", 10),
        ("author-consensus", 0),  # намеренно не в этой шкале
        ("", 0),
        (None, 0),
        ("unknown_made_up_source", 0),
    ])
    def test_rank_matches_expected(self, source, expected_rank):
        assert evidence.series_source_rank(source) == expected_rank

    def test_folder_dataset_beats_folder_hierarchy(self):
        assert evidence.series_source_rank("folder_dataset") > \
            evidence.series_source_rank("folder_hierarchy")

    def test_filename_prefix_beats_other_filename(self):
        assert evidence.series_source_rank("filename_prefix") > \
            evidence.series_source_rank("filename_named_arc")

    def test_any_folder_beats_any_filename_beats_any_metadata(self):
        assert evidence.series_source_rank("folder_metadata_confirmed") > \
            evidence.series_source_rank("filename_prefix")
        assert evidence.series_source_rank("filename_named_arc") > \
            evidence.series_source_rank("metadata")

    def test_composite_suffix_does_not_change_rank(self):
        assert evidence.series_source_rank("folder_dataset+series-consensus") == \
            evidence.series_source_rank("folder_dataset")


class TestPickWinner:
    def test_folder_beats_filename_beats_metadata(self):
        candidates = [
            ("Из меты", "metadata"),
            ("Из имени файла", "filename"),
            ("Из папки", "folder_dataset"),
        ]
        assert evidence.pick_winner(candidates) == ("Из папки", "folder_dataset")

    def test_empty_values_ignored(self):
        candidates = [
            ("", "folder_dataset"),
            (None, "filename"),
            ("Реальное значение", "metadata"),
        ]
        assert evidence.pick_winner(candidates) == ("Реальное значение", "metadata")

    def test_ties_resolved_by_first_seen_order(self):
        candidates = [
            ("Первый файл-кандидат", "filename"),
            ("Второй файл-кандидат", "filename_series_root"),
        ]
        # Оба — тир 2 (filename*); побеждает первый по порядку.
        assert evidence.pick_winner(candidates) == ("Первый файл-кандидат", "filename")

    def test_no_candidates_returns_none(self):
        assert evidence.pick_winner([]) is None

    def test_all_empty_returns_none(self):
        assert evidence.pick_winner([("", ""), (None, "metadata")]) is None

    def test_custom_rank_fn(self):
        # series_source_rank различает filename_prefix и прочий filename_*
        # (source_tier — нет); с явным rank_fn это различение доступно.
        candidates = [
            ("Дуга из имени файла", "filename_named_arc"),
            ("Ведущий номер", "filename_prefix"),
        ]
        assert evidence.pick_winner(
            candidates, rank_fn=evidence.series_source_rank,
        ) == ("Ведущий номер", "filename_prefix")

    def test_tie_break_picks_max_key_among_equal_rank(self):
        candidates = [
            ("Короткое", "filename"),
            ("Более длинное имя", "filename_named_arc"),
        ]
        # Оба тир 2 по source_tier — без tie_break победил бы первый по
        # порядку; с tie_break по длине строки побеждает более длинный.
        assert evidence.pick_winner(
            candidates, tie_break=lambda c: len(str(c[0])),
        ) == ("Более длинное имя", "filename_named_arc")

    def test_tie_break_does_not_override_higher_rank(self):
        candidates = [
            ("Куда длиннее, но метаданные", "metadata"),
            ("Папка", "folder_dataset"),
        ]
        # tie_break сравнивает только среди РАВНЫХ по рангу — не должен
        # позволить более длинной metadata-строке обойти папку.
        assert evidence.pick_winner(
            candidates, tie_break=lambda c: len(str(c[0])),
        ) == ("Папка", "folder_dataset")


def _rec(file_path, series_source):
    return BookRecord(
        file_path=file_path, file_title="Т", metadata_authors="Автор",
        proposed_author="Автор", author_source="folder_dataset",
        metadata_series="", proposed_series="Серия" if series_source else "",
        series_source=series_source,
    )


class TestFolderHasSignalSynthetic:
    def test_folder_with_one_folder_dataset_record_has_signal(self):
        records = [
            _rec(r"Автор\Серия\1.fb2", "folder_dataset"),
            _rec(r"Автор\Серия\2.fb2", ""),
        ]
        result = evidence.folder_has_signal(records)
        assert result[str(Path(r"Автор\Серия"))] is True

    def test_folder_with_no_folder_derived_record_has_no_signal(self):
        records = [
            _rec(r"Автор\1.fb2", ""),
            _rec(r"Автор\2.fb2", "metadata"),
        ]
        result = evidence.folder_has_signal(records)
        assert result[str(Path(r"Автор"))] is False

    def test_no_series_folder_counts_as_signal(self):
        # Явное "здесь серии нет" — тоже папочный сигнал, не "нет сигнала".
        records = [_rec(r"Автор\Вне серий\1.fb2", "no_series_folder")]
        result = evidence.folder_has_signal(records)
        assert result[str(Path(r"Автор\Вне серий"))] is True

    def test_records_without_file_path_are_skipped(self):
        records = [_rec("", "folder_dataset")]
        assert evidence.folder_has_signal(records) == {}

    def test_custom_source_attr_for_author_source(self):
        # folder_has_signal параметризуема по атрибуту-источнику — можно
        # использовать её и для author_source, не только series_source.
        rec = _rec(r"Автор\1.fb2", "")
        rec.author_source = "folder_dataset"
        result = evidence.folder_has_signal([rec], source_attr="author_source")
        assert result[str(Path(r"Автор"))] is True


class TestFolderHasSignalOnRealTest2Data:
    """Проверка на реальных данных из Test2 — та же папка, что стала
    мотивирующим случаем для самого бага (см. баг №109, продолжение,
    "Начинается вьюга.fb2"): 23 самостоятельных рассказа прямо в
    корневой папке автора, ни один не в подпапке серии — папка НЕ должна
    получить папочный сигнал, несмотря на то, что у некоторых файлов
    metadata_series непустая.
    """

    FOLDER = r"C:\Users\dmitriy.murov\Downloads\TriblerDownloads\Test2\Пехов Алексей - Сборник"

    @pytest.fixture(scope="class")
    def records(self):
        service = RegenCSVService(_config_path())
        return service.generate_csv(self.FOLDER, output_csv_path=None)

    def test_flat_author_root_has_no_folder_signal(self, records):
        # generate_csv(folder, ...) возвращает file_path ОТНОСИТЕЛЬНО
        # переданной папки — файлы прямо в корне дают parent="." (без
        # подпапки серии).
        result = evidence.folder_has_signal(records)
        assert result.get(".") is False

    def test_kindret_subfolder_has_folder_signal(self, records):
        # "Киндрэт - Пехов,Бычкова, Турчанинова" — подпапка серии,
        # folder_dataset должен был сработать хотя бы для одного файла.
        result = evidence.folder_has_signal(records)
        assert result.get("Киндрэт - Пехов,Бычкова, Турчанинова") is True


class TestDecisionLog:
    def test_log_decision_appends_to_record(self):
        rec = _rec(r"Автор\1.fb2", "")
        evidence.log_decision(rec, "проверка А: условие выполнено")
        evidence.log_decision(rec, "проверка Б: условие НЕ выполнено")
        assert rec.decision_log == [
            "проверка А: условие выполнено",
            "проверка Б: условие НЕ выполнено",
        ]

    def test_log_decision_silently_noops_without_decision_log_attr(self):
        # Лёгкие SimpleNamespace-обёртки (GUI-превью компилятора) не
        # обязаны нести decision_log — log_decision не должна падать.
        from types import SimpleNamespace
        ns = SimpleNamespace(file_path="x.fb2")
        evidence.log_decision(ns, "что угодно")  # не должно бросить исключение
        assert not hasattr(ns, "decision_log")

    def test_format_decision_log_joins_entries_numbered(self):
        rec = _rec(r"Автор\1.fb2", "")
        evidence.log_decision(rec, "первое решение")
        evidence.log_decision(rec, "второе решение")
        assert evidence.format_decision_log(rec) == (
            "1. первое решение\n2. второе решение"
        )

    def test_format_decision_log_empty_is_explanatory_not_blank(self):
        rec = _rec(r"Автор\1.fb2", "")
        result = evidence.format_decision_log(rec)
        assert result != ""
        assert "пуст" in result.lower()
