"""Регрессия для `AuthorName._extract_parts()` (name_normalizer.py) —
docs/quality-roadmap.md, баг №109 ("хрупкость каскада", часть 3).

Реальный случай (наст. автор Richard Osman, метаданные "Ричард Томас
Осман" — Имя Отчество-подобное_среднее_имя Фамилия): при РОВНО одном
известном имени на одном из краёв 3-словной записи ("Ричард" — известное
имя, "Осман" и "Томас" — нет), код ветки "0 или 2+ неизвестных слов"
проверял только случай "ни один край не известен" (тогда считаем, что
порядок уже Фамилия Имя) — если известен РОВНО один край, код молча
оставлял дефолт lastname=последнее слово/firstname=первое слово,
ТЕРЯЯ среднее слово целиком (ни в фамилии, ни в имени). Найдено через
golden-снапшот тест — прогон полной фикстур-библиотеки давал разные
результаты ("Осман Ричард Томас" vs "Осман Ричард") в зависимости от
того, был ли известных-имён кэш `AuthorName._get_known_names()` уже
заполнен к моменту разбора этого конкретного имени (см. соседний фикс
в regen_csv.py: `AuthorNormalizer(self.settings)` теперь конструируется
сразу в `RegenCSVService.__init__`, а не только внутри PASS 3+, чтобы
`AuthorName.set_config_path()` вызывался раньше PASS 2's
prebuild_author_cache()).
"""
from fb2parser_core.name_normalizer import AuthorName
from fb2parser_web.fb2parser_bridge import _config_path
from fb2parser_core.settings_manager import SettingsManager


def setup_module(module):
    # male_names/female_names живут в app_settings.json, не в config.json.
    AuthorName.set_config_path(SettingsManager(_config_path()).app_settings_path)


class TestMiddleWordKeptWhenOnlyOneEndIsKnownName:
    def test_known_firstname_first_unknown_middle_and_surname_kept(self):
        # "Ричард" — известное имя, "Томас"/"Осман" — нет. Реальный автор
        # Richard Osman: должно дать фамилию "Осман", имя "Ричард Томас"
        # (среднее слово не должно теряться).
        a = AuthorName("Ричард Томас Осман")
        assert a.parts == ("Осман", "Ричард Томас", None)
        assert a.normalized == "Осман Ричард Томас"

    def test_known_firstname_last_unknown_surname_kept_without_middle(self):
        # Известное имя в начале, УЖЕ нет середины — не должно сломаться
        # регрессией на 2-словном случае (тот идёт по отдельной ветке).
        a = AuthorName("Иван Кузнецов")
        assert a.normalized == "Кузнецов Иван"

    def test_neither_end_known_unaffected_assumes_already_surname_first(self):
        # Sanity: старое поведение ("ни один край не известен — считаем,
        # что уже Фамилия Имя...") не должно быть задето фиксом.
        a = AuthorName("Линдквист Йон Айвиде")
        assert a.normalized == "Линдквист Йон Айвиде"
