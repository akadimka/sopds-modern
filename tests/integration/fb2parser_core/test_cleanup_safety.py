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
