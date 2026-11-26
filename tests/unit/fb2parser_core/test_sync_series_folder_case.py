"""Папка серии, которая уже есть в библиотеке с другим регистром или «ё», —
та же серия (2026-11-26, основной сервер: «Тайный город» Панова лёг рядом с
«Тайный Город» отдельной папкой — Linux различает регистр).

Имена в тестах различаются и регистром, и «ё»/«е»: на Windows регистр не
различается файловой системой, а «ё» — различается, так что проверка
одинаково осмысленна на обеих системах.
"""
from pathlib import Path

from fb2parser_core.synchronization import SynchronizationService


def _service(library: Path) -> SynchronizationService:
    service = SynchronizationService.__new__(SynchronizationService)
    service.library_path = library
    service.log_callback = lambda msg: None
    service.logger = None
    return service


def test_existing_series_folder_with_other_case_and_yo_is_reused(tmp_path):
    author = tmp_path / "Фантастика" / "Панов Вадим"
    (author / "Звёздный Город").mkdir(parents=True)
    service = _service(tmp_path)

    assert service._existing_child_variant(author, "звездный город") == "Звёздный Город"
    assert service._existing_child_variant(author, "Звёздный Город") == "Звёздный Город"
    assert service._existing_child_variant(author, "Другая серия") == "Другая серия"
    assert service._existing_child_variant(tmp_path / "нет такой", "x") == "x"


def test_two_variants_are_not_guessed(tmp_path):
    author = tmp_path / "Фантастика" / "Автор"
    (author / "Ёлка").mkdir(parents=True)
    (author / "елка").mkdir()
    assert _service(tmp_path)._existing_child_variant(author, "ЕЛКА") == "ЕЛКА"


def test_series_in_library_sees_folder_with_other_case(tmp_path):
    series_dir = tmp_path / "Фантастика" / "Панов Вадим" / "Звёздный Город"
    series_dir.mkdir(parents=True)
    (series_dir / "Панов Вадим - Звёздный Город (в 41 книгах).fb2.zip").write_bytes(b"x")

    found = _service(tmp_path)._series_in_library("Панов Вадим", "", "звездный город")

    assert found == {"Фантастика": ("Панов Вадим", 1)}
