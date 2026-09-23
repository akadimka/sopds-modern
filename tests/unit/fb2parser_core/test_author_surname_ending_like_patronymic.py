"""Регрессия для `AuthorName._extract_parts()` (name_normalizer.py) —
docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (замечен пользователем в CSV): "«Морской» цикл
(Александр Конторович)" — папка серии, откуда `folder_dataset` берёт
автора "Александр Конторович" (Имя Фамилия). Собственные метаданные
файлов подтверждают: "Александр Сергеевич Конторович" (Имя Отчество
Фамилия) — "Конторович" однозначно фамилия. Но при развороте порядка
слов для РОВНО 2-словного имени проверка "последнее слово похоже на
отчество" (окончание "-ович"/"-евич") срабатывала раньше, чем более
точная эвристика по известным именам — "Конторович" ошибочно
распознавался как ОТЧЕСТВО без фамилии вовсе, "Александр Конторович"
оставался неперевёрнутым (Имя Фамилия вместо принятого в библиотеке
Фамилия Имя). Многие настоящие русские (и не только) фамилии
оканчиваются на "-ович"/"-евич" той же морфологией, что и отчества
(Конторович, Рабинович, Абрамович, Юркевич) — а "Имя Отчество" без
фамилии вообще — крайне редкая, вырожденная форма записи автора.
"""
from fb2parser_core.name_normalizer import AuthorName
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def setup_module(module):
    # male_names/female_names живут в app_settings.json, не в config.json
    # (см. AuthorNormalizer._init_author_name() — тот же путь и то же
    # обоснование).
    AuthorName.set_config_path(SettingsManager(_config_path()).app_settings_path)


class TestSurnameEndingLikePatronymicNotMistakenForPatronymic:
    def test_kontorovich_recognized_as_surname(self):
        a = AuthorName("Александр Конторович")
        assert a.normalized == "Конторович Александр"

    def test_other_common_surnames_with_same_ending(self):
        for given, surname in [
            ("Давид", "Рабинович"),
            ("Марк", "Абрамович"),
        ]:
            a = AuthorName(f"{given} {surname}")
            assert a.normalized == f"{surname} {given}", a.normalized

    def test_genuine_three_word_first_patronymic_surname_unaffected(self):
        # Sanity: полное "Имя Отчество Фамилия" (баг не связан с этим
        # случаем — обрабатывается отдельной, более ранней веткой) —
        # не должно быть задето фиксом.
        a = AuthorName("Александр Сергеевич Конторович")
        assert a.normalized == "Конторович Александр Сергеевич"

    def test_already_surname_first_unaffected(self):
        # Sanity: уже правильный порядок (Фамилия Имя) не должен
        # случайно перевернуться обратно.
        a = AuthorName("Конторович Александр")
        assert a.normalized == "Конторович Александр"
