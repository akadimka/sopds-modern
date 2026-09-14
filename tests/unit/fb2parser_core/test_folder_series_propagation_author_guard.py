"""Регрессия для `Pass4Consensus.execute()` (перенос серии от донора всем
файлам папки без своей серии) — docs/quality-roadmap.md, баг №73.

Реальный случай (замечен пользователем в логе синхронизации): папка
"Серия - «Библиотека мировой литературы» (СЗКЭО) Оптмизир\\FB2" — это
общая скан-папка издательского сборника, а не папка одной серии. В ней
рядом лежат 12 настоящих томов "Тысяча и одна ночь. В 12 томах" (автор
"Автор Неизвестен -- Народные Сказки") и 3 СОВЕРШЕННО ОТДЕЛЬНЫЕ, никак
не связанные книги других авторов — "Бронте Эмилия — Грозовой перевал",
"Стивенс Джеймс — Ирландские предания", "Чехов Антон — Юмористические
рассказы" (у всех metadata_series пустая, proposed_series пустая — они
сами по себе не входят ни в какую серию).

"FOLDER SERIES PROPAGATION" переносит серию от донора (в данном случае —
от 12 томов сказок, получивших "Тысяча и одна ночь. В 12 томах" через
folder_meta_consensus) на ЛЮБУЮ запись той же папки без своей серии —
без проверки, что автор записи вообще имеет отношение к этой серии. Это
привело к тому, что Бронте/Стивенс/Чехов ошибочно получили
proposed_series="Тысяча и одна ночь. В 12 томах", из-за чего они позже
были неверно сгруппированы синхронизацией как часть чужого сборника.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path

_FOLDER = r"Серия - «Библиотека мировой литературы» (СЗКЭО) Оптмизир\FB2"


def _tom(n, roman):
    # proposed_series/series_source заданы напрямую как "folder_meta_consensus"
    # — именно в таком виде эти поля были у реальных 12 томов в CSV
    # (docs/quality-roadmap.md, баг №73), независимо от того, каким именно
    # более ранним механизмом (голосование по большинству metadata_series в
    # папке, либо любой другой) они были в это состояние приведены. Это
    # изолирует тест именно от "FOLDER SERIES PROPAGATION" — механизма,
    # который безусловно раздаёт proposed_series от такого донора всем
    # файлам папки без своей серии.
    return BookRecord(
        file_path=fr"{_FOLDER}\Тысяча и одна ночь. Том {roman} - 2022.fb2",
        file_title="Тысяча и одна ночь. В 12 томах",
        metadata_authors="Народные сказки",
        proposed_author="Автор Неизвестен -- Народные Сказки",
        author_source="folder_dataset",
        metadata_series="Тысяча и одна ночь. В 12 томах",
        proposed_series="Тысяча и одна ночь. В 12 томах",
        series_source="folder_meta_consensus",
        series_number=str(n),
    )


def _standalone(author, title, filename):
    return BookRecord(
        file_path=fr"{_FOLDER}\{filename}.fb2",
        file_title=title,
        metadata_authors=author,
        proposed_author=author,
        author_source="filename",
        metadata_series="",
        proposed_series="",
        series_source="",
        series_number="",
    )


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


class TestFolderSeriesPropagationRequiresRelatedAuthor:
    def test_unrelated_standalone_books_not_pulled_into_anthology_series(self):
        romans = ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ", "Ⅷ", "Ⅸ", "Ⅹ", "Ⅺ", "Ⅻ"]
        records = [_tom(i + 1, r) for i, r in enumerate(romans)]
        records += [
            _standalone("Бронте Эмилия", "Грозовой перевал", "Бронте Э. - Грозовой перевал - 2022"),
            _standalone("Стивенс Джеймс", "Ирландские предания", "Стивенс Дж. - Ирландские предания - 2022"),
            _standalone("Чехов Антон", "Юмористические рассказы", "Чехов А.П. - Юмористические рассказы - 2021"),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        by_author = {r.proposed_author: r for r in records}
        assert by_author["Бронте Эмилия"].proposed_series == ""
        assert by_author["Стивенс Джеймс"].proposed_series == ""
        assert by_author["Чехов Антон"].proposed_series == ""
        # Настоящие тома серии по-прежнему корректно получают её.
        assert all(
            r.proposed_series == "Тысяча и одна ночь. В 12 томах"
            for r in records if r.proposed_author == "Автор Неизвестен -- Народные Сказки"
        )

    def test_propagation_still_works_for_same_author_without_own_series(self):
        # Sanity: легитимный случай не должен сломаться — донор уже получил
        # серию из папки (folder_dataset), а соседняя запись ТОГО ЖЕ автора
        # без своей серии (например, отдельная книга того же автора без
        # номера тома) по-прежнему должна получать серию от донора.
        records = [
            BookRecord(
                file_path=fr"{_FOLDER}\Автор Тест - Серия Книга 1.fb2",
                file_title="Серия Книга 1", metadata_authors="Автор Тест",
                proposed_author="Автор Тест", author_source="folder_dataset",
                metadata_series="", proposed_series="Своя Серия",
                series_source="folder_dataset", series_number="1",
            ),
            BookRecord(
                file_path=fr"{_FOLDER}\Автор Тест - Отдельная повесть.fb2",
                file_title="Отдельная повесть", metadata_authors="Автор Тест",
                proposed_author="Автор Тест", author_source="filename",
                metadata_series="", proposed_series="", series_source="",
                series_number="",
            ),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        extra = next(r for r in records if r.file_title == "Отдельная повесть")
        assert extra.proposed_series == "Своя Серия"
