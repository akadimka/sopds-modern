"""Регрессионные тесты для fb2parser_core.regen_csv на реальных "сложных"
структурах папок, урезанных до минимального веса (текст книги и обложки
вырезаны, см. tests/data/regen_library и scripts/build_regen_fixtures.py).

Каждый набор здесь — реально встречавшийся в библиотеке пользователя
краевой случай, разобранный и починенный вручную; тест защищает от
повторной регрессии того же класса ошибок без ручного full-library diff.
"""
from pathlib import Path

import pytest

from fb2parser_core import regen_csv
from fb2parser_web.fb2parser_bridge import _config_path

LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "data" / "regen_library"


@pytest.fixture(scope="module")
def records():
    service = regen_csv.RegenCSVService(_config_path())
    return service.generate_csv(str(LIBRARY_ROOT), output_csv_path=None)


def _by_suffix(records, *path_parts):
    """Найти запись, чей file_path заканчивается указанными частями пути."""
    suffix = str(Path(*path_parts))
    matches = [r for r in records if r.file_path.endswith(suffix)]
    assert len(matches) == 1, f"expected exactly 1 match for {suffix!r}, got {len(matches)}"
    return matches[0]


class TestPastFlatSeries:
    """«Пасть/Война родов» — папка-датасет авторитетна и не должна
    расщепляться на подсерию из-за смены названия внутри книги.
    """

    FOLDER = ("Романович (Пастырь) Роман - Сборник",
              "Пасть [=Обманувший смерть] (завершён)")

    @pytest.mark.parametrize("filename, expected_number", [
        ("Пасть 1. Обманувший смерть.fb2", "1"),
        ("Пасть 4. Война родов. Начало.fb2", "4"),
        ("Пасть 8. Война родов. Финал.fb2", "8"),
    ])
    def test_single_flat_series(self, records, filename, expected_number):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == "Пасть"
        assert rec.series_number == expected_number


class TestEltterusMultiArc:
    """«Отзвуки серебряного ветра» — нумерованные подпапки-дуги: номер
    файла внутри дуги валиден, даже если совпадает с номером самой дуги
    (баг: sub_ordinal обнулялся при filename_prefix == parent_num).
    """

    FOLDER = ("Русский фантастический боевик", "Эльтеррус Иар (Тертышный Игорь)",
              "Отзвуки серебряного ветра")

    def test_arc_subfolder_series_and_numbering(self, records):
        rec1 = _by_suffix(records, *self.FOLDER, "2. Мы — есть!", "1. Честь.fb2")
        rec2 = _by_suffix(records, *self.FOLDER, "2. Мы — есть!", "2. Вера.fb2")
        assert rec1.proposed_series.endswith("2. Мы — есть!")
        assert rec1.series_number == "1"
        assert rec2.series_number == "2"
        assert rec1.series_number_source == "filename_prefix"
        assert rec2.series_number_source == "filename_prefix"


class TestBessonovSubfolderHierarchy:
    """«Мир Алекса Королёва» — вложенные подпапки-подсерии, каждая со
    своей независимой последовательной нумерацией.
    """

    FOLDER = ("Русский фантастический боевик", "Бессонов Алексей", "Мир Алекса Королёва")

    def test_each_subfolder_numbers_independently(self, records):
        r1 = _by_suffix(records, *self.FOLDER, "3. Хикки", "1. Чертова дюжина ангелов.fb2")
        r2 = _by_suffix(records, *self.FOLDER, "3. Хикки", "2. Статус миротворца.fb2")
        assert r1.series_number == "1"
        assert r2.series_number == "2"
        assert r1.proposed_series.endswith("3. Хикки")


class TestAuthorInitialsStripped:
    """«Роберт Дж. Сойер» — среднее имя-инициал должно сворачиваться
    в фамилию+имя без потери структуры (fallback только когда основной
    парсер вернул пусто).
    """

    def test_middle_initial_folder_author(self, records):
        rec = _by_suffix(records, "Роберт Дж. Сойер", "Сойер. Без следа.fb2")
        assert rec.proposed_author == "Роберт Сойер"


class TestArcRomanNumeral:
    """«Пастырь. Арка 2.0. Том I» — «2.0» это версия дуги (не должна
    читаться как «том 2»), а «Том I» — римская цифра, даёт номер 1.
    """

    FOLDER = ("Романович (Пастырь) Роман - Сборник", "Вне циклов")

    def test_decimal_arc_and_roman_volume(self, records):
        rec = _by_suffix(records, *self.FOLDER, "Пастырь. Арка 2.0. Том I.fb2")
        assert rec.series_number == "1"


class TestSmolinZeroPaddedPrefix:
    """«01. Название.fb2» — ведущий номер с нулём (в отличие от «1. Название.fb2»)
    не распознаётся как filename_prefix и уходит в менее надёжный
    filename_series_refix. Обнаружено этим fixture-набором, ещё не починено —
    xfail документирует известный пробел вместо того чтобы тест тихо падал
    при будущем фиксе (тогда его надо снять).
    """

    FOLDER = ("Серия - «Попаданец - СИ»", "Смолин Павел")

    @pytest.mark.xfail(reason="zero-padded leading number ('01.') not recognized as filename_prefix", strict=True)
    def test_zero_padded_prefix_recognized(self, records):
        rec = _by_suffix(
            records, *self.FOLDER,
            "Смолин Павел - Самый лучший пионер 01. Самый лучший пионер.fb2",
        )
        assert rec.series_number_source == "filename_prefix"


class TestAlphabetIndexFolderAndAuthorSpellingLeak:
    """Баг №48: "Азбука Социальной Фантастики (833)\\С\\Стругацки Аркадий\\*.fb2" —
    два независимых бага давали proposed_series="С\\Стругацки Аркадий":

    1. "С" — папка алфавитного указателя (авторы рассортированы по первой
       букве фамилии) — не была распознана как индексная и текла в серию
       наравне с настоящими подпапками-сериями.
    2. Папка автора "Стругацки Аркадий" (болгарское издание — болгарская
       орфография без архаичного русского окончания "-ий") не совпадала с
       каноничным именем автора "Стругацкий Аркадий" в `_surnames_match_folder()`
       (допуск был только на МНОЖЕСТВЕННОЕ число — "Живов"→"Живовы", не на
       усечённое единственное), поэтому папка автора ошибочно принималась
       за подпапку серии вместо того, чтобы быть распознанной и исключённой.

    Реальные метаданные серии (`<sequence>`) там, где они есть в файле,
    должны браться как есть; там, где их нет — серия должна остаться
    пустой (а не "С\\Стругацки Аркадий").
    """

    FOLDER_A = ("Азбука Социальной Фантастики (833)", "С", "Стругацки Аркадий")
    FOLDER_B = ("Азбука Социальной Фантастики (833)", "С", "Стругацкие Аркадий и Борис")

    def test_no_fake_hierarchical_series_without_metadata(self, records):
        rec = _by_suffix(records, *self.FOLDER_A, "Стругацки Аркадий - Времето на дъжда.fb2")
        assert rec.proposed_series == ""
        assert "С\\" not in (rec.proposed_series or "")
        assert rec.proposed_author == "Стругацкий Аркадий"

    def test_real_metadata_series_preserved(self, records):
        rec = _by_suffix(
            records, *self.FOLDER_B,
            "Стругацкие Аркадий и Борис - Забытый эксперимент.fb2",
        )
        assert rec.proposed_series == "Предполуденный цикл"

    def test_coauthor_folder_author_correct(self, records):
        rec = _by_suffix(
            records, *self.FOLDER_B,
            "Стругацкие Аркадий и Борис - Град обреченный.fb2",
        )
        assert rec.proposed_author == "Стругацкий Аркадий, Стругацкий Борис"
        assert rec.proposed_series == ""


class TestAlphabetIndexFolderMisreadAsAuthor:
    """Баг №49 (тот же класс, что и Баг №48, но в АВТОРСКОЙ, а не серийной
    эвристике): "Азбука Социальной Фантастики (833)\\Ю\\Юдин Борис
    Петрович\\*.fb2" — папка алфавитного указателя "Ю" сама по себе
    совпала со словом в словаре мужских имён (редкое имя "Ю" в
    male_names) и была принята precache'ом за автора с низкой
    уверенностью. Настоящая папка автора "Юдин Борис Петрович" на
    следующем уровне после этого считалась "конфликтующей с родителем"
    (не пересекается словами с "Ю") и ошибочно принималась papки за
    подсерию вместо автора — итог: proposed_author="Ю.",
    proposed_series="Юдин Борис Петрович".
    """

    FOLDER_YUDIN = ("Азбука Социальной Фантастики (833)", "Ю", "Юдин Борис Петрович")
    FOLDER_YUNGER = ("Азбука Социальной Фантастики (833)", "Ю", "Юнгер Эрнст")
    FOLDER_YURIEV = ("Азбука Социальной Фантастики (833)", "Ю", "Юрьев Зиновий Юрьевич")

    def test_single_letter_index_not_mistaken_for_author(self, records):
        rec = _by_suffix(records, *self.FOLDER_YUDIN, "Юдин Борис Петрович - Город, который сошел с ума.fb2")
        assert rec.proposed_author != "Ю."
        assert rec.proposed_series != "Юдин Борис Петрович"
        assert rec.proposed_series == ""

    def test_real_author_folder_recognized(self, records):
        rec = _by_suffix(records, *self.FOLDER_YUNGER, "Юнгер Эрнст - Гелиополь.fb2")
        assert rec.proposed_author == "Юнгер Эрнст"
        assert rec.proposed_series == ""

    def test_patronymic_author_folder_recognized(self, records):
        rec = _by_suffix(records, *self.FOLDER_YURIEV, "Юрьев Зиновий Юрьевич - Человек под копирку.fb2")
        assert rec.proposed_series == ""
        assert rec.proposed_author != "Ю."
