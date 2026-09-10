"""Регрессия для `RegenCSVService._extract_series_from_folder_name()` —
"Цикл «Название»"/"Серия «Название»"/"Сага «Название»" — служебное
слово-маркер, означающее "это серия", а не часть самого названия.

Реальный случай (Евгений Щепетнов / "Компиляции циклов"): 19 из 20 папок
называются по шаблону «Цикл «Название». Книги N-M» — например «Цикл
«Ботаник». Книги 1-3» означает "серия Ботаник, книги 1-3", а не то, что
серию зовут "Цикл «Ботаник»". Без этого правила слово "Цикл" и хвост
"Книги N-M" протекали в proposed_series целиком.

Одна папка — исключение: «Истринский цикл (Лекарь). Книги 1-4» — слово
"цикл" здесь идёт ПОСЛЕ имени и без кавычек, это часть настоящего
составного названия, а не маркер-обёртка; трогать её нельзя.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _svc():
    return RegenCSVService(_config_path())


class TestCycleMarkerStrippedFromQuotedName:
    def test_tsikl_guillemets_stripped(self):
        assert _svc()._extract_series_from_folder_name(
            "Цикл «Ботаник». Книги 1-3"
        ) == "Ботаник"

    def test_seriya_marker_also_stripped(self):
        assert _svc()._extract_series_from_folder_name(
            "Серия «Слава». Книги 1-5"
        ) == "Слава"

    def test_saga_marker_also_stripped(self):
        assert _svc()._extract_series_from_folder_name(
            "Сага «Грифон». Книги 1-2"
        ) == "Грифон"

    def test_straight_quotes_also_recognized(self):
        assert _svc()._extract_series_from_folder_name(
            'Цикл "Ботаник". Книги 1-3'
        ) == "Ботаник"

    def test_no_trailing_tail_still_works(self):
        assert _svc()._extract_series_from_folder_name("Цикл «Ботаник»") == "Ботаник"


class TestNameWithBareCycleWordNotTouched:
    def test_cycle_word_after_name_without_quotes_is_untouched(self):
        # "Истринский цикл" — составное имя, "цикл" не маркер-обёртка.
        result = _svc()._extract_series_from_folder_name(
            "Истринский цикл (Лекарь). Книги 1-4"
        )
        assert result == "Истринский цикл (Лекарь). Книги 1-4"
