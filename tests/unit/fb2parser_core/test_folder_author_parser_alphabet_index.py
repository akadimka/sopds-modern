"""Регрессия для `parse_author_from_folder_name()` — docs/quality-
roadmap.md, баг №49.

Реальный случай (замечен пользователем в CSV): "Азбука Социальной
Фантастики (833)\\Ю\\Юдин Борис Петрович\\*.fb2" — папка алфавитного
указателя "Ю" сама по себе совпала со словом в словаре мужских имён
(редкое однобуквенное имя "Ю" есть в `male_names`) и кэшировалась
precache'ом как автор с низкой уверенностью. Настоящая папка автора
"Юдин Борис Петрович" на следующем уровне после этого считалась
"конфликтующей с родителем" (не пересекается словами с "Ю") и
ошибочно принималась за подсерию вместо автора — итог:
`proposed_author="Ю."`, `proposed_series="Юдин Борис Петрович"`.

Папка из ровно одной буквы никогда не является настоящим именем автора
(даже когда эта буква сама по себе есть в словаре имён) — она всегда
алфавитный индекс.
"""
from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def _name_sets():
    settings = SettingsManager(_config_path())
    male = set(n.lower() for n in settings.get_male_names())
    female = set(n.lower() for n in settings.get_female_names())
    return male, female


class TestSingleLetterFolderNeverParsedAsAuthor:
    def test_letter_that_is_also_a_dictionary_name_rejected(self):
        male, female = _name_sets()
        # Sanity: "ю" реально есть в словаре имён — иначе баг вообще не
        # мог бы воспроизвестись (сам по себе гарантирует релевантность теста).
        assert "ю" in male or "ю" in female
        assert parse_author_from_folder_name("Ю", male_names=male, female_names=female) == ""

    def test_other_single_letters_also_rejected(self):
        male, female = _name_sets()
        for letter in ("С", "А", "Б", "К"):
            assert parse_author_from_folder_name(letter, male_names=male, female_names=female) == ""

    def test_real_two_word_author_folder_still_parses(self):
        male, female = _name_sets()
        # Sanity: фикс не должен ломать обычные, настоящие авторские папки.
        result = parse_author_from_folder_name("Юнгер Эрнст", male_names=male, female_names=female)
        assert result != ""
