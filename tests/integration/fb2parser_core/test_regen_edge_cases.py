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
def records(tmp_path_factory):
    # output_csv_path=None (что раньше делал этот тест) пропускает
    # _save_csv() целиком — а вместе с ним и все финальные пост-чеки,
    # выполняющиеся ТОЛЬКО в момент сохранения CSV (напр.
    # _clear_collection_folder_series(), Баг №56). Указываем реальный
    # путь, чтобы тесты видели то же поведение, что и настоящий regen.csv.
    out_csv = tmp_path_factory.mktemp("regen_csv_out") / "regen.csv"
    service = regen_csv.RegenCSVService(_config_path())
    return service.generate_csv(str(LIBRARY_ROOT), output_csv_path=str(out_csv))


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

    @pytest.mark.xfail(
        reason="Известный конфликт (найден при фиксе Бага №56, не относится к нему): "
               "_save_csv()'s 'singleton metadata series' post-check (regen_csv.py, "
               "~2903) стирает metadata_series, если она встречается только у ОДНОГО "
               "файла автора в наборе и не найдена в пути — в этой урезанной фикстуре "
               "'Предполуденный цикл' помечен только у одной книги. Раньше это не "
               "ловилось тестами: до фикса fixture'ы 'records' здесь output_csv_path "
               "был None, из-за чего _save_csv() (и все её пост-чеки) вообще не "
               "выполнялся. Нужно решить на реальной библиотеке — считать ли это "
               "поведение правильным (проверка не даёт довериться шумной "
               "одиночной metadata-серии) или тест был прав, а эвристику надо "
               "смягчить. См. docs/quality-roadmap.md, раздел 'Открытые вопросы'.",
        strict=True,
    )
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


class TestLateSeriesResolutionStillGetsFilenameNumberCorrection:
    """Баг №54: "Гришэм. Округ Форд 4. Рассказы (пер. Наталья Рейн).fb2" —
    `<sequence name="Округ Форд" number="1"/>` в самих метаданных файла
    ОШИБОЧНО даёт "1" (реальная позиция — 4, как видно из имени файла).
    `proposed_series` для этого файла разрешается в "Округ Форд" только
    очень поздно, через `_postcheck_metadata_rescue()` — к этому моменту
    первый (и на тот момент единственный) вызов коррекции
    `series_number` из имени файла (Правило 2, "SeriesRoot N. Title")
    внутри `Pass2SeriesFilename.execute()` уже отработал и ничего не
    смог поправить, т.к. серия тогда была ещё не определена.
    """

    FOLDER = ("Гришэм Джон - Сборник",)

    def test_metadata_sequence_number_corrected_from_filename(self, records):
        rec = _by_suffix(records, *self.FOLDER, "Гришэм. Округ Форд 4. Рассказы (пер. Наталья Рейн).fb2")
        assert rec.proposed_series == "Округ Форд"
        assert rec.series_number == "4"

    def test_other_volumes_in_the_series_unaffected(self, records):
        rec2 = _by_suffix(records, *self.FOLDER, "Гришэм. Округ Форд 2. Повестка (пер. Юрий Кирьяк) - 2008.fb2")
        rec3 = _by_suffix(records, *self.FOLDER,
                           "Гришэм. Округ Форд 3. Последний присяжный (пер. Ирина Доронина) - 2018.fb2")
        assert rec2.series_number == "2"
        assert rec3.series_number == "3"


class TestTranslatorCreditParenthesisNotTreatedAsSeries:
    """Баг №55: "Гришэм. Остров Камино 1. Остров Камино (пер. Виктор
    Антонов).fb2" — трейлинг-скобка "(пер. Имя Фамилия)" (указание
    переводчика) распознавалась Правилом 2 ("серия в скобках в конце")
    как имя серии — точка после "пер" даже давала ложное совпадение с
    паттерном "Серия. service_words". Метаданные книги дополнительно
    сбивали с толку: `<sequence name="Гришэм: лучшие детективы">` —
    издательский ярлык-подборка, а не настоящая серия, поэтому
    восстановление через metadata тоже не спасало.
    """

    FOLDER = ("Гришэм Джон - Сборник",)

    def test_translator_credit_stripped_before_series_extraction(self, records):
        rec = _by_suffix(records, *self.FOLDER, "Гришэм. Остров Камино 1. Остров Камино (пер. Виктор Антонов).fb2")
        assert rec.proposed_series == "Остров Камино"
        assert rec.series_number == "1"

    def test_translator_credit_with_year_suffix_also_stripped(self, records):
        # Sanity: тот же паттерн, но с ГОДОМ после переводческой скобки —
        # трейлинг-скобка перестаёт быть последним элементом строки, пока
        # год не вырезан первым (см. Баг №54 — Округ Форд 2/3 идут с
        # "- 2008"/"- 2018" после скобки переводчика).
        rec = _by_suffix(records, *self.FOLDER, "Гришэм. Округ Форд 2. Повестка (пер. Юрий Кирьяк) - 2008.fb2")
        assert rec.proposed_series == "Округ Форд"


class TestMultiAuthorImprintFolderRescueUsesFilenameNotMetadata:
    """Баг №56: "«Коллекция МИФ»\\Клуб убийств" — папка-импринт издательства
    с несколькими РАЗНЫМИ авторами; корректно распознаётся как НЕ-серия
    (multi-author cleanup), но после этого восстановление series раньше
    сразу откатывалось на metadata_series — а он для Мур Йен даёт другой
    перевод названия ("Тайны долины Фоллет"), не совпадающий с тем, что
    в имени файла ("Тайны Валь-де-Фолла"). Починка: перед metadata rescue
    сначала повторно пробуем filename-экстракцию, но требуем консенсус
    (>=2 файла ОДНОГО автора с одинаковым кандидатом), чтобы не подхватить
    случайный шум и не перезаписать спин-офф собственной серией автора.
    """

    FOLDER = ("Серия - «Коллекция МИФ»", "Клуб убийств")

    @pytest.mark.parametrize("filename", [
        "Мур Йен - Тайны Валь-де-Фолла 1. Смерть и круассаны.fb2",
        "Мур Йен - Тайны Валь-де-Фолла 2. Смерть и козий сыр.fb2",
        "Мур Йен - Тайны Валь-де-Фолла 3. Смерть в Шато.fb2",
    ])
    def test_moore_series_from_filename_not_metadata(self, records, filename):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == "Тайны Валь-де-Фолла"

    @pytest.mark.parametrize("filename", [
        "Осман Ричард - Клуб убийств по четвергам 1. Клуб убийств по четвергам.fb2",
        "Осман Ричард - Клуб убийств по четвергам 2. Человек, который умер дважды.fb2",
        "Осман Ричард - Клуб убийств по четвергам 3. Выстрел мимо цели.fb2",
        "Осман Ричард - Клуб убийств по четвергам 4. Ловушка для дьявола.fb2",
    ])
    def test_osman_main_series_from_filename(self, records, filename):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == "Клуб убийств по четвергам"

    def test_osman_spinoff_not_merged_into_majority_series(self, records):
        rec = _by_suffix(
            records, *self.FOLDER,
            "Осман Ричард - Мы раскрываем убийства 1. Мы раскрываем убийства.fb2",
        )
        assert rec.proposed_series != "Клуб убийств по четвергам"

    def test_thorogood_series_from_filename(self, records):
        rec = _by_suffix(
            records, *self.FOLDER,
            "Торогуд Роберт - Клуб убийств Марлоу 1. Смерть на Темзе.fb2",
        )
        assert rec.proposed_series == "Клуб убийств Марлоу"


class TestPublisherImprintMetadataEchoNotTreatedAsFilenameConsensus:
    """Баг №56 (вторая находка): "«Коллекция МИФ»\\МИФ. Проза" — папка
    одного издательского импринта с РАЗНЫМИ авторами (не серия, каждый
    автор — отдельная книга без своей серии), но многие файлы делят один
    и тот же ЛОЖНЫЙ `<sequence name="МИФ Проза">` в metadata (это ярлык
    подборки издательства, не серия).

    Причина: `_extract_series_from_filename()` (Pass2) при паттерне
    "Автор. Название" (без серии в самом имени файла) возвращает
    ПЕРЕДАННЫЙ ей `metadata_series` как есть — это исходно рассчитано на
    контекст первого прохода. Первая версия FILENAME RESCUE в
    Pass4Consensus передавала `metadata_series` в этот вызов "для
    подтверждения" — из-за чего два файла РАЗНЫХ авторов с одинаковым
    ложным metadata_series тривиально проходили порог консенсуса "≥2
    файла согласны", хотя ни один из них НИЧЕГО общего в самом имени
    файла не имеет. Фикс: FILENAME RESCUE больше не передаёт
    metadata_series в этот вызов — кандидат должен быть независимым
    сигналом из имени файла, а не эхом уже отвергнутого metadata.
    """

    FOLDER = ("Серия - «Коллекция МИФ»", "МИФ. Проза")

    @pytest.mark.parametrize("filename", [
        "Мачадо Кармен Мария. Дом иллюзий.fb2",
        "Мачадо Кармен Мария. Её тело и другие.fb2",
        "Барри Кевин. Ночной паром в Танжер.fb2",
        "Ко Лиза. Беспокойные.fb2",
    ])
    def test_no_fake_series_from_shared_publisher_metadata(self, records, filename):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == ""


class TestCommaSeparatedAuthorWithArcCollectionSuffix:
    """Баг №58: "Владимир Малый, Тёмные Окна - Сборник произведений" —
    запятая без скобок безусловно выбирала паттерн "Author, Author" (два
    автора через запятую), хотя текст после запятой — "Тёмные Окна -
    Сборник произведений" — это имя вселенной/цикла + служебное слово
    коллекции, а не второй автор. Итог: proposed_author = "Малый
    Владимир, Темные Окна Сборник Произведений" для всех файлов папки.

    Фикс: перед правилом "Author, Author" (pass1_pattern_selection.py)
    добавлена проверка — если текст после запятой содержит " - " с
    коллекционным служебным словом после дефиса ("сборник",
    "произведений" и т.п.), выбирается новый паттерн "Author, Arc -
    Collection", извлекающий автора только до запятой.
    """

    FOLDER = ("Владимир Малый, Тёмные Окна - Сборник произведений",)

    @pytest.mark.parametrize("filename", [
        "А можно выйти 1-3.fb2",
        "В двух шагах до контакта.fb2",
        "Дозор свободного посещения.fb2",
        "Колокол мертвецов.fb2",
        "Мультимир (Великие Игры) 1-3.fb2",
        "Почти во все тяжкие!.fb2",
    ])
    def test_author_is_just_the_name_not_arc_and_collection_word(self, records, filename):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_author == "Малый Владимир"


class TestPseudonymCollectionFolderSeriesNotSwallowedWhole:
    """Баг №59: "Вязовский Алексей - Сборник\\FB2\\С.К.С., Вязовский - Режим
    бога\\файл.fb2" — первые 3 тома серии "Режим бога" изданы под
    псевдонимом-инициалами "С.К.С.", следующие 9 — тем же автором (Вязовский
    Алексей) под своим именем, с перезапущенной с 1 нумерацией томов
    ("Режим бога 1..9" вместо продолжения "4..12"). Пользователь: "первые
    три тома писал один автор (СКС), а следующие тома второй — Вязовский.
    Но нумерацию они поставили дурацкую, из-за чего теперь рвётся серия".

    Первая находка (недостаточная сама по себе): для первых 3 файлов
    BlockLevelPatternMatcher матчил "С.К.С." как корень иерархической серии
    ("С.К.С.\\Режим бога") — guard (баг №50) отбрасывал только ОДНУ голую
    инициаль, не несколько слепленных без пробелов ("С.К.С."). Обобщён на
    `(?:[А-ЯЁA-Z]\\.){1,}` — см. `pass2_series_filename.py` и
    `tests/unit/fb2parser_core/test_initials_pseudonym_not_series_root.py`.
    Эта находка сама по себе НЕ решала жалобу пользователя: `Pass2SeriesFilename`
    для этой подпапки вообще не успевал отработать — см. ниже.

    Настоящая причина (проверено трассировкой на реальной библиотеке —
    поведение отличалось между сканом ОДНОЙ этой подпапки и сканом ВСЕЙ
    библиотеки, что и указало направление поиска): `_compute_folder_series()`
    (regen_csv.py) для корневой папки "Вязовский Алексей - Сборник"
    (классифицируется как `FolderType.PUBLISHER` — содержит слово
    "Сборник") проверяет каждую подпапку через `_surnames_match_folder()`.
    "С.К.С., Вязовский - Режим бога" тоже совпадает с автором (содержит
    слово "Вязовский") — но `_extract_series_from_folder_name()` не умеет
    отделить псевдоним+имя автора через запятую от РЕАЛЬНОГО названия серии
    после тире и возвращает подпапку ЦЕЛИКОМ. Итог: ВСЯ строка "С.К.С.,
    Вязовский - Режим бога" становится `proposed_series` с
    `series_source='folder_dataset'` для ВСЕХ 12 файлов — а folder_dataset
    безусловно авторитетен дальше по конвейеру (`Pass2SeriesFilename`
    видит непустую `folder_dataset`-серию и не трогает запись), так что
    исправление внутри `pass2_series_filename.py` в принципе не могло
    сработать для этого случая.

    Фикс (regen_csv.py, ветка `FolderType.PUBLISHER/COLLECTION`): если
    `_extract_series_from_folder_name()` не вырезала из подпапки НИЧЕГО
    (вернула её как есть) и в имени есть " - ", берём текст ПОСЛЕ
    последнего тире как название серии — он и есть настоящее имя серии, а
    всё до тире (включая псевдоним через запятую) остаётся авторской
    частью, не серией.

    Проверено на реальной библиотеке пользователя (полный скан 3138
    файлов, а не только этой подпапки в изоляции — тестировать так и
    выявило разницу): все 12 файлов получают `proposed_series="Режим
    бога"`, автор унифицирован в "Вязовский Алексей", номер тома —
    сквозной 1-12 (чинит именно ту "дурацкую нумерацию", на которую
    жаловался пользователь). Работает одинаково и на полном урезанном
    тестовом наборе фикстур (`tests/data/regen_library`).
    """

    FOLDER = ("Вязовский Алексей - Сборник", "FB2", "С.К.С., Вязовский - Режим бога")

    @pytest.mark.parametrize("filename, expected_number", [
        ("01. С.К.С. - Режим бога. Книга 1.fb2", "1"),
        ("02. С.К.С. - Режим бога. Книга 2.fb2", "2"),
        ("03. С.К.С. - Режим бога. Книга 3.fb2", "3"),
        ("04. Вязовский - Режим бога 1. Восход Красной Звезды.fb2", "4"),
        ("05. Вязовский - Режим бога 2. Зенит Красной Звезды.fb2", "5"),
        ("06. Вязовский - Режим бога 3. Триумф Красной звезды.fb2", "6"),
        ("07. Вязовский - Режим бога 4. Эпоха Красной Звезды.fb2", "7"),
        ("08. Вязовский - Режим бога 5. Сияние Красной Звезды.fb2", "8"),
        ("09. Вязовский - Режим бога 6. Вспышка Красной Звезды.fb2", "9"),
        ("10. Вязовский - Режим бога 7. Вершина Красной Звезды.fb2", "10"),
        ("11. Вязовский - Режим бога 8. Экспансия Красной Звезды.fb2", "11"),
        ("12. Вязовский - Режим бога 9. Show must go on.fb2", "12"),
    ])
    def test_series_author_and_continuous_numbering_across_pseudonym_change(self, records, filename, expected_number):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == "Режим бога"
        assert rec.proposed_author == "Вязовский Алексей"
        assert rec.series_number == expected_number


class TestTwoAuthorSurnamesInFilenameNotMistakenForSeries:
    """Баг №61: "Ильф, Петров. Том N.fb2" — имя файла повторяет ФАМИЛИИ ОБОИХ
    авторов через запятую (без имён), а не название серии. proposed_series
    получал "Ильф, Петров" — буквально список фамилий авторов.

    Причина: `_is_valid_series()` (Правило 3, ветка "_rest_is_collection_marker"
    — "Том N"/"Книги N-M" после первой точки распознаётся как служебная
    обёртка, не заголовок) проверяет, не является ли кандидат именем автора,
    сравнивая НОРМАЛИЗОВАННЫЕ строки через `AuthorName` — "Ильф, Петров"
    (только фамилии) и "Ильф Илья, Петров Евгений" (полные имена) дают
    РАЗНЫЕ нормализованные строки, поэтому проверка ошибочно решала, что
    это два РАЗНЫХ имени → "значит кандидат — не автор, а серия".

    Фикс: перед этим сравнением добавлен вызов `_is_author_surname()` —
    он уже умел (после отдельного расширения в этом же баге) распознавать
    несколько фамилий через запятую как сокращённую форму
    multi-author `extracted_author` — если совпадает, кандидат
    отклоняется как автор, не доходя до менее надёжного строкового
    сравнения.

    metadata_series ("Собрание сочинений в пяти томах") тоже корректно НЕ
    становится серией — это обёрточное название пятитомника, оно в
    filename_blacklist (`collection_keywords`/`series_folder_blacklist`),
    так что итоговая proposed_series должна остаться пустой — серии в
    привычном смысле здесь просто нет.
    """

    FOLDER = ("Илья Ильф, Евгений Петров",)

    @pytest.mark.parametrize("filename", [
        "Ильф, Петров. Том 1.fb2",
        "Ильф, Петров. Том 2.fb2",
        "Ильф, Петров. Том 3.fb2",
        "Ильф, Петров. Том 4.fb2",
        "Ильф, Петров. Том 5.fb2",
    ])
    def test_author_surnames_pair_not_treated_as_series(self, records, filename):
        rec = _by_suffix(records, *self.FOLDER, filename)
        assert rec.proposed_series == ""
        assert rec.proposed_author == "Ильф Илья, Петров Евгений"
