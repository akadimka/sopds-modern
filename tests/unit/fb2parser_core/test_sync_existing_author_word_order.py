"""Автор с теми же словами в другом порядке — docs/quality-roadmap.md, баг №178.

До №176 латинские имена переставлялись («Ascold Flow» → «Flow Ascold»),
теперь — нет. В библиотеке уже лежат папки в старом порядке, исходный
порядок по ним не восстановить (метаданные переписаны синхронизацией).
Решение пользователя (вариант А): новая книга идёт в существующую папку с
теми же словами, а не в соседнюю новую.
"""
from fb2parser_core.synchronization import SynchronizationService


def _sync(library):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc._log = lambda msg: None
    svc.library_path = library
    return svc


def test_same_words_other_order_reuses_existing_folder(tmp_path):
    (tmp_path / "Фантастика" / "Flow Ascold").mkdir(parents=True)
    (tmp_path / "Фантастика" / "Иванов Иван").mkdir()
    sync = _sync(tmp_path)
    assert sync._existing_author_variant("Фантастика", "Ascold Flow") == "Flow Ascold"
    assert sync._existing_author_variant("Фантастика", "Иван Иванов") == "Иванов Иван"
    assert sync._existing_author_variant("Фантастика", "Flow Ascold") == "Flow Ascold"


def test_other_genre_new_author_or_ambiguous_unchanged(tmp_path):
    for name in ("Flow Ascold", "Jin Woo", "Woo Jin"):
        (tmp_path / "Фантастика" / name).mkdir(parents=True)
    sync = _sync(tmp_path)
    assert sync._existing_author_variant("Детектив", "Ascold Flow") == "Ascold Flow"
    assert sync._existing_author_variant("Фантастика", "Leon Hart") == "Leon Hart"
    assert sync._existing_author_variant("Фантастика", "Woo Jin") == "Woo Jin"   # обе есть — как есть
