"""Регрессия для двух связанных багов в извлечении/консенсусе серии
(docs/quality-roadmap.md, баг №13) — обнаружено пользователем через
реальную группу "Лисина Александра": пре-компилированный файл целой серии
«Времена» ("Времена. Книги 1-10.fb2", папка "Сборники") получал неверное
имя после синхронизации, и логика компиляции не распознавала его как уже
существующий эквивалент 10 отдельных файлов серии «Времена».

1. `Pass2SeriesFilename._extract_series_from_filename()` (Rule 3):
   однословную серию перед служебной обёрткой "Книги N-M"/"Том N" (без
   второго "заголовочного" сегмента) ошибочно принимал за фамилию автора
   (Rule 3, `_is_author_pattern`), после чего Rule 3B извлекал в качестве
   "серии" само служебное слово-обёртку ("Книги") вместо реальной серии
   ("Времена") — файл выпадал из группировки по серии целиком.

2. `Pass4Consensus.execute()`'s "SERIES SUFFIX CORRECTION": даже после
   фикса (1) "Времена" (filename-источник) ошибочно "исправлялось" на
   СОВСЕМ другую, но совпадающую последним словом серию того же автора
   ("Тёмные времена") — простое текстовое совпадение хвостового слова не
   доказывает, что короткое имя это обрезанный префикс длинного (в
   отличие от намеренного случая коррекции "Миха"→"Я - Миха", где полное
   имя реально присутствует в имени файла). Итог: 3 реальные книги
   "Тёмные времена" рисковали быть удалены как "покрытые" 10-томным
   файлом при синхронизации.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author, series="", series_source="", metadata_series="", series_number=""):
    return BookRecord(
        file_path=path, file_title=path, metadata_authors=author,
        proposed_author=author, author_source="folder_dataset",
        proposed_series=series, series_source=series_source,
        metadata_series=metadata_series, series_number=series_number,
    )


class TestSingleWordSeriesBeforeCollectionWrapperNotMisread:
    """"Времена. Книги 1-10.fb2" — однословная серия «Времена» перед
    служебной обёрткой "Книги N-M" должна извлекаться как есть, а не как
    "Книги" (обёрточное слово).
    """

    def _extractor(self):
        return Pass2SeriesFilename(Logger(), config_path=_config_path())

    def test_series_before_books_wrapper_extracted_correctly(self):
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Времена. Книги 1-10.fb2", proposed_author="Лисина Александра",
        )
        assert result == "Времена"

    def test_series_before_tom_wrapper_extracted_correctly(self):
        """Тот же класс: одиночная книга "Серия. Том N" (без служебного
        слова "компиляция"/диапазона) — тоже должна давать серию, а не
        оставаться нераспознанной (реальный случай: "Уроборос. Том 1.fb2").
        """
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Уроборос. Том 1.fb2", proposed_author="Громов Виктор",
        )
        assert result == "Уроборос"


class TestSuffixCorrectionRequiresFullNameInFilename:
    """SERIES SUFFIX CORRECTION не должна путать однословное совпадение
    ("времена" — последнее слово в "Тёмные времена") с обрезанным
    префиксом реальной длинной серии.
    """

    AUTHOR = "Лисина Александра"

    def _records(self):
        return [
            # 10 книг реальной, самостоятельной серии "Времена" — folder_dataset,
            # сильный источник.
            *[
                _rec(f"Времена\\{n:02d}.fb2", self.AUTHOR, series="Времена",
                     series_source="folder_dataset")
                for n in range(1, 11)
            ],
            # Пре-компилированный файл всей серии "Времена" — серия определена
            # из имени файла (filename), это и есть цель SUFFIX CORRECTION.
            _rec("Сборники\\Времена. Книги 1-10.fb2", self.AUTHOR,
                 series="Времена", series_source="filename", series_number="1-10"),
            # Совсем другая, самостоятельная серия того же автора — только
            # последнее слово совпадает с "Времена".
            _rec("Тёмные времена\\01 Враг.fb2", self.AUTHOR,
                 series="Тёмные времена", series_source="folder_dataset"),
            _rec("Тёмные времена\\02 Попутчик.fb2", self.AUTHOR,
                 series="Тёмные времена", series_source="folder_dataset"),
        ]

    def test_unrelated_series_not_used_as_suffix_correction_source(self):
        records = self._records()
        Pass4Consensus(Logger(), settings=SettingsManager(_config_path())).execute(records)
        precompiled = next(r for r in records if "Времена. Книги 1-10" in r.file_path)
        assert precompiled.proposed_series == "Времена", (
            f"'{precompiled.file_path}' was wrongly relabeled to "
            f"'{precompiled.proposed_series}' via a coincidental word-suffix match"
        )

    def test_genuine_prefix_truncation_still_corrected(self):
        """Контрольный случай (из докстринга существующего кода):
        "Я - Миха 1. Дикарь.fb2" (filename-серия "Миха") рядом с
        подтверждённым "Я - Миха" того же автора — тут полное имя РЕАЛЬНО
        присутствует в имени файла, коррекция должна по-прежнему срабатывать.
        """
        records = [
            _rec("Я - Миха\\1. Дикарь.fb2", "Автор Тест",
                 series="Я - Миха", series_source="folder_dataset"),
            _rec("Я - Миха 2. Чужой.fb2", "Автор Тест",
                 series="Миха", series_source="filename"),
        ]
        Pass4Consensus(Logger(), settings=SettingsManager(_config_path())).execute(records)
        corrected = next(r for r in records if r.file_path == "Я - Миха 2. Чужой.fb2")
        assert corrected.proposed_series == "Я - Миха"
