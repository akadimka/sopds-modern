"""Папка «Автор (пер. Переводчик)» — docs/quality-roadmap.md, баг №171.

«Yoonaanee Maiks (пер. Грек-Максим)»: имени «Yoonaanee Maiks» нет в
словаре — папка шла в шаблон «Series (Author)», автором становился
переводчик («Пер Грек-Максим»).
"""
import pytest

from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name

_NAMES = {"алекс", "вадим"}


@pytest.mark.parametrize("folder, author", [
    ("Yoonaanee Maiks (пер. Грек-Максим)", "Yoonaanee Maiks"),
    ("Yoonaanee Maiks (перевод Грек-Максим)", "Yoonaanee Maiks"),
    ("Сигел-Джейсон (соавт. Миллер-Кирстен)", "Сигел-Джейсон"),
    # прежние шаблоны со скобками не меняются
    ("МВП-2 (1) Одиссея (Чернов)", "Чернов"),
    ("Орлов Алекс (Дарищев Вадим)", "Орлов Алекс"),
    ("Пером и шпагой (Иванов)", "Иванов"),
])
def test_translator_or_coauthor_in_brackets(folder, author):
    assert parse_author_from_folder_name(folder, male_names=_NAMES, female_names=set()) == author
