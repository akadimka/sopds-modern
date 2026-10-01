"""FILENAME PREFIX AUTHOR CONSENSUS (`Pass4Consensus.execute`): имя файла
с латинскими буквами-двойниками должно совпадать с кириллическим автором
соседнего файла.

Таблица `_MIXED_SCRIPT_NORM` («Latin→Cyrillic lookalikes») была набрана
кириллицей ('ZzАВЕКМНОРСТХ' → то же самое) и применялась после lower(),
поэтому из всех двойников работал только z→з: «Cергей» с латинской C
не совпадало с «Сергей».
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=author,
        proposed_author=author, author_source="metadata" if author else "",
        metadata_series="", proposed_series="", series_source="",
    )


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


def _run(unknown_stem):
    records = [
        _rec(r"Сборник\Мохов Сергей. Первая.fb2", "Мохов Сергей"),
        _rec("Сборник\\" + unknown_stem + ".fb2", "[unknown]"),
    ]
    Pass4Consensus(Logger(), settings=_settings()).execute(records)
    return records[1]


def test_latin_lookalikes_in_filename_match_cyrillic_author():
    # М, о, х, С, е, р — латинские
    record = _run("Moxoв Ceргeй. Вторая")
    assert record.proposed_author == "Мохов Сергей"
    assert record.author_source == "filename"


def test_different_author_still_not_matched():
    record = _run("Махов Сергей. Вторая")
    assert record.proposed_author == "[unknown]"
