"""Управляющие символы из метаданных — не в имена файлов и папок (баг №162).

Сервер 2026-10-18: «Кейкичи Осака - Чудовище маяка\\nи другие
хонкаку.fb2.zip», «Рив Артур - Крейг Кеннеди, профессор. Крейг Кеннеди,
\\nпрофессор–детектив т. 1.fb2.zip» — перевод строки из <book-title>
попадал в имя файла: подсчёт `find | wc -l` завышался, такие имена ломают
скрипты и неудобны в любом файловом менеджере.
"""
from fb2parser_core.fb2_utils import replace_control_chars
from fb2parser_core.synchronization import (
    SynchronizationService,
    _sanitize_path_component,
)


def test_newline_and_tab_become_a_single_space():
    assert replace_control_chars("Чудовище маяка\nи другие хонкаку") == "Чудовище маяка и другие хонкаку"
    assert replace_control_chars("Крейг Кеннеди, \nпрофессор") == "Крейг Кеннеди, профессор"
    assert replace_control_chars("Серия\t\r\nТом") == "Серия Том"


def test_original_double_spaces_are_kept():
    # имена вида «Сопряжение  Чернильный маг 3» уже лежат в библиотеке — не менять
    assert replace_control_chars("Сопряжение  Чернильный маг 3") == "Сопряжение  Чернильный маг 3"


def test_file_name_and_folder_names_have_no_control_chars():
    assert SynchronizationService._safe("Чудовище маяка\nи другие: хонкаку") == "Чудовище маяка и другие_ хонкаку"
    assert _sanitize_path_component("Крейг\nКеннеди", "x") == "Крейг Кеннеди"
    assert _sanitize_path_component("\n\t", "Без серии") == "Без серии"
