"""Регрессия для `Pass4Consensus.execute()` — docs/quality-roadmap.md,
баг №109 (продолжение).

METADATA RESCUE ("после очистки издательских серий восстанавливаем
metadata_series, если запись осталась без серии") задумывался только для
записей, у которых серию ТОЛЬКО ЧТО стёрла соседняя ветка "MULTI-AUTHOR
SERIES FOLDER CLEANUP" (издательская/жанровая папка-импринт с несколькими
авторами). Но проверка `if record.proposed_series or not
record.metadata_series: continue` не отличала это от записи, у которой
proposed_series пуст был ИЗНАЧАЛЬНО — просто потому, что файл лежит прямо
в корневой папке ОДНОГО автора и никогда не попадал в папочный сигнал о
серии вовсе (реальный случай: "Начинается вьюга.fb2" в "Пехов Алексей -
Сборник\\", metadata_series="Хроники Сиалы" — реальная серия, но
существующая в ДРУГОМ месте библиотеки; файл не должен получать серию
из голых метаданных).

Оба сценария дают одинаковое `proposed_series == ''`, но означают разное —
"мета только подтверждает найденную серию, не придумывает её с нуля".
Фикс: и FILENAME RESCUE, и METADATA RESCUE применяются теперь ТОЛЬКО к
записям, реально очищенным веткой "MULTI-AUTHOR SERIES FOLDER CLEANUP".
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author, series, metadata_series):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=author,
        proposed_author=author, author_source="folder_dataset",
        metadata_series=metadata_series, proposed_series=series,
        series_source="folder_dataset" if series else "",
    )


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


class TestLoneFileInSingleAuthorFolderGetsNoMetadataRescue:
    """"Начинается вьюга.fb2" — один автор в папке, никогда не входит в
    "MULTI-AUTHOR SERIES FOLDER CLEANUP" (папка не многоавторская), значит
    и не является законной целью METADATA RESCUE."""

    def test_no_folder_signal_stays_without_series(self):
        # Третий сосед без metadata_series нужен, чтобы FOLDER METADATA
        # CONSENSUS (отдельный, более ранний блок в execute(), строки
        # ~1337-1432) не сработал сам по себе на 2-файловой папке, где
        # порог "большинства" (max(1, len(grp)*0.5)) тривиально
        # выполняется даже одним-единственным совпадением — это скрыло
        # бы, что именно проверяем: RESCUE-каскад (строки ~1072-1174),
        # а не другой, отдельный механизм с похожей слабостью.
        records = [
            _rec(r"Пехов Алексей - Сборник\Начинается вьюга.fb2",
                 "Пехов Алексей", "", "Хроники Сиалы"),
            _rec(r"Пехов Алексей - Сборник\Дождь.fb2",
                 "Пехов Алексей", "", "Другая метка"),
            _rec(r"Пехов Алексей - Сборник\Пряха.fb2",
                 "Пехов Алексей", "", ""),
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[0].proposed_series == ""
        assert records[0].series_source == ""
        assert records[1].proposed_series == ""
        assert records[1].series_source == ""


class TestMetadataRescueStillWorksAfterGenuineImprintClearing:
    """Позитивный случай (не должен сломаться фиксом): "Fanzon. Век
    магии..." — реальная авторская серия, стёртая из-за совпадения с
    именем многоавторской издательской папки, должна по-прежнему
    восстанавливаться из metadata_series."""

    def test_genuinely_cleared_record_still_rescued_from_metadata(self):
        records = [
            _rec(r"Серия - «Тест»\Мур Йен - Книга.fb2",
                 "Мур Йен", "Тест", "Изгой"),
            _rec(r"Серия - «Тест»\Осман Ричард - Книга.fb2",
                 "Осман Ричард", "Тест", ""),
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[0].proposed_series == "Изгой"
        assert records[0].series_source == "metadata"
