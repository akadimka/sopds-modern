"""Страховки очистки (сервер 2026-10-28, прогон компилятора по всей библиотеке).

- 22 решения очистки удаляли более полную версию: «Слесарь (в 22 книгах)»
  уходил в дубли, «(в 15 книгах)» оставался — у .fb2.zip число книг из имени
  не читалось, а 15/22 текста выше порога. Дубль больше всего, что остаётся,
  удаляется, только если совпадает почти целиком.
- Переименование оставленного файла брало автора группы, а разбор ошибался
  («Милан» вместо Arladaar) — в библиотеке появилось бы «Милан - Arladaar -
  Милан (Ноналогия)». Автор группы не совпадает с папкой — не переименовываем.
"""
import pytest

from fb2parser_core.fb2_compiler import CompilationGroup, FB2CompilerService
from tests.integration.fb2parser_core.test_third_party_omnibus import _omnibus

pytestmark = pytest.mark.django_db


def _cleanup(author, series, kept, dups, volume_range=""):
    return CompilationGroup(author=author, series=series, books=[], order_determined=True,
                            volume_range=volume_range, duplicate_paths=list(dups), kept_paths=[kept],
                            cleanup_only=True)


def test_bigger_duplicate_is_not_deleted(tmp_path):
    folder = tmp_path / "Фантастика" / "Белов Иннокентий" / "Слесарь"
    kept = _omnibus(folder / "Белов Иннокентий - Слесарь (в 3 книгах).fb2", "Слесарь", "Белов Иннокентий", [1, 2, 3])
    fuller = _omnibus(folder / "Белов Иннокентий - Слесарь (в 4 книгах).fb2", "Слесарь", "Белов Иннокентий",
                      [1, 2, 3, 4])
    copy = _omnibus(folder / "Белов Иннокентий - Слесарь. Копия.fb2", "Слесарь", "Белов Иннокентий", [1, 2])
    group = _cleanup("Белов Иннокентий", "Слесарь", kept, [fuller, copy])
    assert FB2CompilerService()._verify_duplicate_paths(group, tmp_path, {})
    assert group.duplicate_paths == [copy]


@pytest.mark.parametrize("author, renamed", [("Милан", False), ("Arladaar", True)])
def test_kept_file_is_renamed_only_under_its_own_author(tmp_path, author, renamed):
    folder = tmp_path / "Фантастика" / "Arladaar" / "Милан"
    kept = _omnibus(folder / "Arladaar - Милан (Пенталогия).fb2", "Милан", "Arladaar", [1, 2])
    FB2CompilerService().compile_group(_cleanup(author, "Милан", kept, [], "1-9"), None, delete_sources=True)
    assert sorted(p.name for p in folder.iterdir()) == (
        [f"{author} - Милан (Ноналогия).fb2"] if renamed else [kept.name])


def test_rename_does_not_overwrite_an_existing_file(tmp_path):
    folder = tmp_path / "Фантастика" / "Arladaar" / "Милан"
    kept = _omnibus(folder / "Arladaar - Милан (Пенталогия).fb2", "Милан", "Arladaar", [1, 2])
    other = _omnibus(folder / "Arladaar - Милан (Ноналогия).fb2", "Милан", "Arladaar", [3])
    other_text = other.read_bytes()
    FB2CompilerService().compile_group(_cleanup("Arladaar", "Милан", kept, [], "1-9"), None, delete_sources=True)
    assert kept.exists() and other.read_bytes() == other_text


@pytest.mark.parametrize("name, series, volume_range", [
    ("Поселягин Владимир - Я попал (Пенталогия).fb2", "Я - попал", "1-5"),
    ("Поселягин Владимир - Я попал (Декалогия в 16 книгах).fb2", "Я попал", "1-10"),
])
def test_name_already_in_our_format_is_kept(tmp_path, name, series, volume_range):
    """Сервер 2026-10-28: очистка переименовывала «Я попал (Пенталогия)» в
    «Я - попал» (написание серии из разбора) и «(Декалогия в 16 книгах)» в
    «(Декалогия)» — имя уже по нашему образцу, переименовывать нечего."""
    folder = tmp_path / "Фантастика" / "Поселягин Владимир" / "Я попал"
    kept = _omnibus(folder / name, "Я попал", "Поселягин Владимир", [1, 2])
    FB2CompilerService().compile_group(_cleanup("Поселягин Владимир", series, kept, [], volume_range), None,
                                       delete_sources=True)
    assert [p.name for p in folder.iterdir()] == [name]
