"""`RegenCSVService._postcheck_filename_prefix_pattern()` — баг №125.

«Автор. A.fb2» + «Автор. A. B.fb2» — это почти всегда роман A и сборник
«два в одном», а не серия A (на Test2 все 45 таких пар были ложными).
Серия по префиксу ставится, только если с A начинаются хотя бы два
разных файла.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(stem):
    return BookRecord(
        file_path=f"Детективы\\{stem}.fb2", file_title=stem, metadata_authors="Иванов Иван",
        proposed_author="Иванов Иван", author_source="metadata",
        metadata_series="", proposed_series="", series_source="",
    )


def _run(*stems):
    service = RegenCSVService(_config_path())
    service.records = [_rec(s) for s in stems]
    service._postcheck_filename_prefix_pattern()
    return {r.file_title: (r.proposed_series, r.series_source) for r in service.records}


def test_single_two_in_one_volume_gets_no_series():
    result = _run("Иванов Иван. Тайна замка", "Иванов Иван. Тайна замка. Проклятие рода")
    assert result["Иванов Иван. Тайна замка"] == ("", "")
    assert result["Иванов Иван. Тайна замка. Проклятие рода"] == ("", "")


def test_two_continuations_make_a_series():
    result = _run(
        "Иванов Иван. Хроники Севера",
        "Иванов Иван. Хроники Севера. Начало",
        "Иванов Иван. Хроники Севера. Возвращение",
    )
    assert {v for v in result.values()} == {("Хроники Севера", "filename_prefix_pattern")}


def test_existing_series_is_reused_as_canonical():
    service = RegenCSVService(_config_path())
    a, b, c = (_rec(s) for s in (
        "Иванов Иван. Хроники Севера",
        "Иванов Иван. Хроники Севера. Начало",
        "Иванов Иван. Хроники Севера. Возвращение",
    ))
    b.proposed_series, b.series_source = "Хроники Севера (цикл)", "filename"
    service.records = [a, b, c]
    service._postcheck_filename_prefix_pattern()
    assert a.proposed_series == c.proposed_series == "Хроники Севера (цикл)"
    assert b.series_source == "filename"


def test_author_folder_comparison_ignores_yo():
    """`_normalize_name_for_comparison` не приводила ё к е: папка «Швырёв
    Владимир» не совпадала с автором «Швырев Владимир»."""
    service = RegenCSVService(_config_path())
    assert (service._normalize_name_for_comparison("Швырёв Владимир")
            == service._normalize_name_for_comparison("Швырев Владимир"))
