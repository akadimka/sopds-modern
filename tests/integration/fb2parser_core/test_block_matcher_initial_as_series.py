"""Регрессия для `Pass2SeriesFilename._extract_series_from_filename()` —
обнаружено пользователем в сгенерированном `regen_Test1.csv`, реальный
файл "Битон М. С. - Хэмиш Макбет 1. Смерть сплетницы.fb2" (автор — Marion
Chesney, пишет под псевдонимом "M.C. Beaton" / "Битон М. С.").

`BlockLevelPatternMatcher` сматчил имя файла по паттерну "Author. Series.
SubSeries. Title", разбив двусоставный псевдоним "М. С." на Author="Битон
М." + Series="С." — второй инициал автора ошибочно принят за отдельное
название серии, из-за чего proposed_series получал "С.\\Хэмиш Макбет"
вместо чистого "Хэмиш Макбет".
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


class TestBlockMatcherRejectsBareInitialAsSeries:
    def _extractor(self):
        return Pass2SeriesFilename(Logger(), config_path=_config_path())

    def test_two_initial_pseudonym_not_split_into_bogus_series(self):
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Битон М. С. - Хэмиш Макбет 1. Смерть сплетницы.fb2",
            metadata_series="Хэмиш Макбет",
            proposed_author="Мэрион Чесни Гиббонс",
        )
        assert result is not None
        assert "\\" not in result
        assert result.strip().rstrip("0123456789 ") == "Хэмиш Макбет"

    def test_second_volume_same_pseudonym(self):
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Битон М. С. - Хэмиш Макбет 2. Смерть негодяя.fb2",
            metadata_series="Хэмиш Макбет",
            proposed_author="Мэрион Чесни Гиббонс",
        )
        assert result is not None
        assert "\\" not in result

    def test_confirmed_by_metadata_still_resolves_correctly(self):
        """"Соловьев С. Ю. - Русскiй детектiвъ" — тот же класс (двусоставный
        "С. Ю."), с metadata_series подтверждением реальная серия находится
        корректно, а не просто отбрасывается."""
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Соловьев С. Ю. - Русскiй детектiвъ 1. Морской узел и счастливый билет.fb2",
            metadata_series="Русскiй детектiвъ",
            proposed_author="Соловьев Сергей",
        )
        assert result is not None
        assert result.strip().rstrip("0123456789 ") == "Русскiй детектiвъ"


class TestNoFallthroughToAnotherBogusAuthorGuessWithoutMetadata:
    """Первая версия фикса просто обнуляла series_from_block и давала
    функции искать дальше — но БЕЗ metadata_series более поздние, менее
    надёжные правила (Rule 3B) сами способны угадать ДРУГОЙ ложный
    кандидат. Реальный случай: "Хьюитт Дж. М. - Идеальная деревня.fb2" +
    "Хьюитт Дж. М. - Прекрасная новая жизнь.fb2" — 2 САМОСТОЯТЕЛЬНЫЕ
    книги без единой серии (разные названия, metadata_series пуста).
    После отбрасывания "М." (тоже голая инициаль автора) Rule 3B выдавал
    "Хьюитт Дж" — тоже фрагмент имени автора, просто не пойманный
    отдельной проверкой `_is_author_surname` (та разбирает по ОДНОМУ
    слову author, не по составным двусловным фрагментам). Без metadata —
    надёжнее вернуть "серии нет" (None), как было до всей этой цепочки
    исправлений, чем идти на очередной необоснованный угад.
    """

    def _extractor(self):
        return Pass2SeriesFilename(Logger(), config_path=_config_path())

    def test_two_standalone_books_no_metadata_get_no_series(self):
        p = self._extractor()
        result = p._extract_series_from_filename(
            "Хьюитт Дж. М. - Идеальная деревня.fb2",
            validate=False,
            metadata_series="",
            proposed_author="М. Хьюитт Дж.",
        )
        assert not result
