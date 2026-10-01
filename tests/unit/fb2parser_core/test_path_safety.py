"""Одна проверка «путь внутри папки» на весь проект (раньше их было пять,
две — через str.startswith с обходом через соседнюю папку)."""
import os

import pytest

from fb2parser_core.path_safety import is_within


def test_inside_and_self(tmp_path):
    (tmp_path / "lib" / "a").mkdir(parents=True)
    assert is_within(tmp_path / "lib" / "a" / "b.fb2", tmp_path / "lib")
    assert is_within(tmp_path / "lib", tmp_path / "lib")


@pytest.mark.parametrize("rel", ["lib2/x.fb2", "lib/../secret.db", "other"])
def test_outside(tmp_path, rel):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib2").mkdir()
    assert not is_within(tmp_path / rel, tmp_path / "lib")


def test_empty_folder_or_path_is_not_within(tmp_path):
    # Пустой корень раньше означал «текущий каталог процесса».
    assert not is_within(tmp_path / "x", "")
    assert not is_within("", tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="регистр важен только на Windows")
def test_case_insensitive_on_windows(tmp_path):
    (tmp_path / "Lib").mkdir()
    assert is_within(str(tmp_path / "lib" / "x.fb2").upper(), tmp_path / "Lib")
