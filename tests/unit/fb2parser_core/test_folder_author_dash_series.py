"""parse_author_from_folder_name — «Автор(ы) - Серия» (баг №135)."""
import pytest

from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name

MALE = {"Гарри", "Том", "Иван", "Питер", "Дэниел"}
FEMALE = {"Робин"}


@pytest.mark.parametrize("folder, expected", [
    ("Гарри Гаррисон, Том Шиппи - Молот и Крест", "Гарри Гаррисон; Том Шиппи"),
    ("Гарри Гаррисон - Молот и Крест", "Гарри Гаррисон"),
    ("Иванов Иван - Война и мир", "Иванов Иван"),
    # удачный прежний разбор целиком не трогаем (инициал сохраняется)
    ("Питер В. Бретт - Война с демонами", "Питер В. Бретт"),
])
def test_author_from_part_before_dash(folder, expected):
    assert parse_author_from_folder_name(folder, MALE, FEMALE) == expected


def test_no_known_name_before_dash_keeps_old_parsing():
    """До тире не имя — разбор как раньше (pre-pass не срабатывает)."""
    folder = "Мир Вальдиры - Кроу"
    assert parse_author_from_folder_name(folder, MALE, FEMALE) == \
        parse_author_from_folder_name(folder, MALE, FEMALE, _split_dash=False)
