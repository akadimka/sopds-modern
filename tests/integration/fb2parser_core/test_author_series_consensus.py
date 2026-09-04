"""Регрессионные тесты для консенсус-логики автора/серии
(`series_processor.SeriesProcessor`, `regen_csv.RegenCSVService`
пост-чеки, `passes.pass4_consensus.Pass4Consensus`) — обнаружены на
реальной библиотеке пользователя (см. docs/quality-roadmap.md, пункт 5),
fb2-файлы недоступны локально, поэтому проверяется на синтетических
BookRecord вместо tests/data/regen_library.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_core.series_processor import SeriesProcessor
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, meta_author, proposed_author, author_source, **kw):
    kw.setdefault("metadata_series", "")
    kw.setdefault("proposed_series", "")
    kw.setdefault("series_source", "")
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


class TestSeriesConsensusFolderPathLeak:
    """extracted_series_candidate по докстрингу в pass1_read_files.py —
    "серия из имени файла", путей папок в ней быть не должно. Но найдена
    реальная запись, где туда попал путь с именем автора-папки
    ("Данильченко-Олег\\Имперский вояж" вместо чистого "Имперский
    вояж") — из-за чего этот "грязный" текст мог навязываться другим
    файлам той же серии через apply_series_consensus(), физически
    разрывая одну серию на несколько при компиляции (тот же класс
    бага, что чинили для Эльтеррус/"Отзвуки серебряного ветра").

    NB: этот тест подтверждает, что при описанном входе результат
    консенсуса чист ("Имперский вояж"), но НЕ воспроизводит byte-в-byte
    исходный триггер бага на реальных файлах — см. docs/quality-roadmap.md,
    пункт 5, баг №3, для деталей и незавершённой части разбора.
    """

    def test_folder_path_stripped_before_consensus_propagation(self):
        sp = SeriesProcessor(_config_path())
        records = [
            _rec("СЕРИЯ.LitRPG\\...\\Данильченко-Олег\\Имперский вояж\\1. Из варяг в небо.fb2",
                 "Данильченко Олег", "Данильченко Олег", "folder_dataset",
                 proposed_series="Имперский вояж", series_source="folder_dataset",
                 extracted_series_candidate="Данильченко-Олег\\Имперский вояж"),
            _rec("Боевая фантастика. Коллекция\\Данильченко. Цикл «Имперский вояж».fb2",
                 "Данильченко Олег", "Данильченко Олег", "metadata"),
        ]
        sp.apply_series_consensus(records)
        assert records[1].proposed_series == "Имперский вояж"
        assert "\\" not in records[1].proposed_series


class TestSubfolderHierarchyAuthorFolderWithMidwordParens:
    """`_postcheck_build_subfolder_hierarchy()` (regen_csv.py) ошибочно
    принимал саму папку автора за "серию верхнего уровня", когда в её
    имени скобки стоят НЕ в конце ("Горъ (Гозалишвили) Василий" — реальная
    фамилия автора вставлена между псевдонимом и именем), а не как целиком
    завершающий суффикс "Псевдоним (Реал)". Guard "дедушка совпадает с
    proposed_author" сравнивал имя папки только после обрезки СКОБОК С
    КОНЦА строки — для середины строки скобки не обрезались, из-за чего
    сравнение не совпадало, guard не срабатывал, и в proposed_series
    протекал весь путь "Автор\\Серия" целиком вместо чистого имени серии.

    Реальный случай: "Русский фантастический боевик\\Горъ (Гозалишвили)
    Василий\\Демон\\1. Демон.fb2" — 5 файлов серии "Демон" получали
    proposed_series="Горъ (Гозалишвили) Василий\\Демон" вместо "Демон".
    """

    def _service(self, records):
        from pathlib import Path
        service = RegenCSVService(_config_path())
        service.work_dir = Path(r"C:\Library")
        service.author_folder_cache = {}  # намеренно пусто — как в реальном случае
        service.records = records
        return service

    def test_author_folder_with_midword_parens_not_treated_as_series(self):
        records = [
            _rec(f"Горъ (Гозалишвили) Василий\\Демон\\{n}. Т{n}.fb2",
                 "Василий Горъ", "Горъ Василий", "folder_dataset",
                 proposed_series="Демон", series_source="folder_dataset")
            for n in range(1, 6)
        ]
        service = self._service(records)
        service._postcheck_build_subfolder_hierarchy()
        for r in records:
            assert r.proposed_series == "Демон", (
                f"'{r.file_path}': proposed_series стало {r.proposed_series!r} — "
                "имя папки автора протекло в серию"
            )
            assert "\\" not in r.proposed_series


class TestSeriesSuffixCorrectionFolderPathLeak:
    """Подтверждено на реальных файлах ("Поселягин Владимир\\Криминал",
    live-проверка на машине с доступом к \\\\turnkey\\Docs\\Books —
    см. docs/quality-roadmap.md, пункт 5, баг №3): изначальный фикс
    apply_series_consensus() (TestSeriesConsensusFolderPathLeak выше) не
    останавливал утечку — на реальной библиотеке она шла через ДРУГОЙ,
    независимый механизм: "SERIES SUFFIX CORRECTION" в
    Pass4Consensus.execute() (pass4_consensus.py).

    На момент, когда этот блок выполняется, proposed_series
    folder_dataset-записи из вложенной "Автор\\Серия" структуры
    ("...\\Поселягин-Владимир\\Криминал\\1. Дон.fb2") ещё содержит сырой
    "Поселягин-Владимир\\Криминал" — POST-CHECK в regen_csv.py, который
    его отрезает, выполняется ПОЗЖЕ, после Pass 4. SUFFIX CORRECTION
    считает этот сырой длинный вариант "полной" версией серии автора и
    "исправляет" соседний чистый filename-файл ("Поселягин Владимир -
    Криминал 1. Дон.fb2", proposed_series="Криминал") до грязного
    "Поселягин-Владимир\\Криминал", т.к. короткое "криминал" оказывается
    суффиксом длинного (endswith-проверка).

    Live-подтверждение (293 реальных файла автора + вся папка
    "Серия - «Боевая фантастика»(ЛенИздат)" as-is, 727 файлов/232
    автора — без фильтрации по автору, иначе multi-author guard в
    pass2_series_filename.py не даёт репрезентативной картины): без
    фикса — 11 протёкших записей (Криминал, Гаврош, Зург, Освобожденный,
    Ремонтник, Собиратель, Сопротивленец), с фиксом — 0.
    """

    def test_raw_folder_dataset_series_not_used_as_suffix_correction_source(self):
        folder_dataset_path = (
            "СЕРИЯ.LitRPG\\Hеофициальная серия книг (разные издательства)"
            "\\Поселягин-Владимир\\Криминал\\1. Дон.fb2"
        )
        clean_filename_path = (
            "Серия - «Боевая фантастика»(ЛенИздат)"
            "\\Поселягин Владимир - Криминал 1. Дон.fb2"
        )
        records = [
            # Сырое, ещё не вычищенное POST-CHECK'ом состояние folder_dataset-записи.
            _rec(folder_dataset_path, "Владимир Геннадьевич Поселягин", "Поселягин Владимир",
                 "folder_dataset", proposed_series="Поселягин-Владимир\\Криминал",
                 series_source="folder_dataset"),
            # Чистая filename-запись той же серии в другой папке.
            _rec(clean_filename_path, "Владимир Геннадьевич Поселягин", "Поселягин Владимир",
                 "filename", proposed_series="Криминал", series_source="filename"),
        ]
        Pass4Consensus(Logger(), settings=SettingsManager(_config_path())).execute(records)
        clean_rec = next(r for r in records if r.file_path == clean_filename_path)
        assert clean_rec.proposed_series == "Криминал"
        assert "\\" not in clean_rec.proposed_series


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
