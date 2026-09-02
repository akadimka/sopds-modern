"""Регрессионные тесты для консенсус-логики автора/серии
(`series_processor.SeriesProcessor`, `regen_csv.RegenCSVService`
пост-чеки) — обнаружены на реальной библиотеке пользователя
(см. docs/quality-roadmap.md, пункт 5), fb2-файлы недоступны локально,
поэтому проверяется на синтетических BookRecord вместо
tests/data/regen_library.
"""
from fb2parser_core.author_normalizer_extended import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_core.series_processor import SeriesProcessor
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, meta_author, proposed_author, author_source, **kw):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=meta_author,
        proposed_author=proposed_author, author_source=author_source, **kw,
    )


class TestAuthorConsensusAnthologyGuard:
    """«Скандинавская линия «НордБук»» — 45-файловая антология с 33
    разными настоящими авторами. Единственный файл с filename-уровня
    сигналом ("Сьон - Скугга-Бальдур.fb2") тривиально проходил проверку
    "консенсус-автор ≥50% high-priority записей" (100% от ОДНОГО голоса)
    и навязывался всем остальным файлам с другими реальными авторами.
    """

    FOLDER = "Серия - «Скандинавская линия»\\Скандинавская линия «НордБук»"

    def _anthology_records(self):
        return [
            _rec(f"{self.FOLDER}\\Сьон - Скугга-Бальдур.fb2",
                 "Сьон Сигурдссон", "Сигурдссон Сьон", "filename+meta_expanded"),
            _rec(f"{self.FOLDER}\\Альвтеген - Ключ.fb2",
                 "Альбин Альвтеген; Карин Альвтеген", "Альбин Альвтеген", "metadata"),
            _rec(f"{self.FOLDER}\\Биргиссон - Х.fb2",
                 "Бергсвейн Биргиссон", "Бергсвейн Биргиссон", "metadata"),
            _rec(f"{self.FOLDER}\\Буман - Y.fb2",
                 "Терез Буман", "Терез Буман", "metadata"),
            _rec(f"{self.FOLDER}\\Гардель - Z.fb2",
                 "Юнас Гардель", "Юнас Гардель", ""),
        ]

    def test_single_high_priority_vote_does_not_override_anthology(self):
        sp = SeriesProcessor(_config_path())
        records = self._anthology_records()
        sp.apply_author_consensus(records)
        for r in records[1:]:
            assert r.proposed_author != "Сигурдссон Сьон", (
                f"{r.file_path} was wrongly overridden by a single-vote consensus"
            )


class TestAuthorConsensusLegitimateCase:
    """Консенсус должен по-прежнему подтверждать автора, когда в папке
    есть ≥2 файлов с надёжным (folder_dataset/filename) источником —
    минимум-2-голоса не должен ломать нормальный случай.
    """

    def test_two_high_priority_votes_still_apply_consensus(self):
        sp = SeriesProcessor(_config_path())
        folder = "Иванов Петр\\Серия Х"
        records = [
            _rec(f"{folder}\\1. Книга.fb2", "Петр Иванов", "Иванов Петр", "folder_dataset"),
            _rec(f"{folder}\\2. Книга.fb2", "Петр Иванов", "Иванов Петр", "folder_dataset"),
            _rec(f"{folder}\\3. Книга.fb2", "Иванов", "Иванов", "metadata"),
        ]
        sp.apply_author_consensus(records)
        assert records[2].proposed_author == "Иванов Петр"
        assert records[2].author_source == "consensus"


class TestSeriesFolderPrefixTrailingQuote:
    """«Серия - «Колычев. Лучшая криминальная драма»» — обрезка префикса
    "Серия - «" оставляла висячую закрывающую » в конце названия серии
    (единственный такой случай во всей 49519-файловой библиотеке —
    подтверждено конкретным grep перед фиксом).
    """

    def test_trailing_closing_quote_stripped(self):
        service = RegenCSVService(_config_path())
        service.records = [
            _rec("Серия - «Колычев. Лучшая криминальная драма»\\Колычев - А ты бы ей отказал.fb2",
                 "Владимир Колычев", "Колычев Владимир", "filename+meta_expanded",
                 proposed_series="Серия - «Колычев. Лучшая криминальная драма»",
                 series_source="folder_hierarchy"),
        ]
        service._postcheck_series_folder_blacklist()
        assert service.records[0].proposed_series == "Колычев. Лучшая криминальная драма"

    def test_nested_quote_pair_keeps_inner_pair_intact(self):
        """"Серия - «Приключения «Икс»»" — обрезаем только внешнюю
        (от префикса) закрывающую кавычку, внутренняя самостоятельная
        пара «Икс» остаётся нетронутой.
        """
        service = RegenCSVService(_config_path())
        service.records = [
            _rec("Серия - «Приключения «Икс»»\\Файл.fb2",
                 "Автор Тест", "Автор Тест", "filename+meta_expanded",
                 proposed_series="Серия - «Приключения «Икс»»",
                 series_source="folder_hierarchy"),
        ]
        service._postcheck_series_folder_blacklist()
        assert service.records[0].proposed_series == "Приключения «Икс»"
