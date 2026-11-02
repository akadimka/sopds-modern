"""Запись тегов в FB2 с encoding="latin-1" при тексте в UTF-8.

Сервер 2026-11-02: шесть книг библиотеки («Мамбурин Харитон - Криндж», «Артемьев
Роман - Отец Народа»…) объявлены latin-1, а текст в них — UTF-8. latin-1
декодирует любые байты, файл читался мешаниной, а синхронизация писала
автора и название с errors='replace' — они становились «????». Теперь такой
файл пишется в UTF-8 с исправленной декларацией; а если новое значение не
записать в настоящей кодировке файла — файл не трогается (как жанр в №139).
"""
from fb2parser_core.fb2_utils import declared_encoding_lies
from fb2parser_core.genre_assign import GenreAssignmentService
from fb2parser_core.synchronization import SynchronizationService

_FB2 = (
    '<?xml version="1.0" encoding="latin-1"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">\n'
    "<description><title-info><genre>sf</genre>"
    "<author><first-name>Old</first-name><last-name>Author</last-name></author>"
    "<book-title>Book</book-title></title-info></description>\n"
    "<body><section><p>{text}</p></section></body></FictionBook>\n"
)


def _service():
    service = SynchronizationService.__new__(SynchronizationService)
    service.log_callback = None
    service.logger = None
    service.stats = {}
    service._log = lambda msg: None
    return service


def _mislabeled(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.format(text="Этот мир безумен.").encode("utf-8"))
    return path


def test_declared_encoding_lies_only_for_non_ascii_valid_utf8():
    assert declared_encoding_lies("Привет".encode("utf-8"), "latin-1")
    assert not declared_encoding_lies("Привет".encode("cp1251"), "windows-1251")
    assert not declared_encoding_lies(b"plain ascii", "latin-1")
    assert not declared_encoding_lies("Привет".encode("utf-8"), "utf-8")
    assert not declared_encoding_lies("Привет".encode("utf-8"), None)


def test_sync_writes_cyrillic_tags_into_mislabeled_utf8_file(tmp_path):
    path = _mislabeled(tmp_path)
    _service()._patch_fb2_tags(path, "Мамбурин Харитон", "Криндж", "Криндж (Тетралогия)")
    data = path.read_bytes()
    text = data.decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<last-name>Мамбурин</last-name>" in text and "Криндж (Тетралогия)" in text
    assert "Этот мир безумен." in text                     # текст книги не задвоен в кодировке
    assert b"?" * 4 not in data


def test_sync_leaves_file_untouched_when_tags_do_not_fit_its_encoding(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.format(text="Café").encode("latin-1"))   # настоящий latin-1
    before = path.read_bytes()
    _service()._patch_fb2_tags(path, "Мамбурин Харитон", "", None)
    assert path.read_bytes() == before


def test_genre_assign_writes_into_mislabeled_utf8_file(tmp_path):
    path = _mislabeled(tmp_path)
    assert GenreAssignmentService()._assign_genre_to_file(path, "Фантастика") is True
    text = path.read_bytes().decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<genre>Фантастика</genre>" in text and "Этот мир безумен." in text


def _with_broken_byte(tmp_path):
    """Как все 6 книг библиотеки: 1–2 битых символа в тексте (опечатки исходника)."""
    path = tmp_path / "book.fb2"
    text = "Этот мир безумен. " * 200
    path.write_bytes(_FB2.format(text=text).encode("utf-8").replace("безумен".encode("utf-8"), b"\xd0\xb1\xb5", 1))
    return path


def test_declared_encoding_lies_tolerates_rare_broken_bytes():
    text = ("Этот мир безумен. " * 200).encode("utf-8")
    assert declared_encoding_lies(text[:100] + b"\xd0" + text[100:], "latin-1")
    assert not declared_encoding_lies(("Этот мир безумен. " * 200).encode("cp1251"), "windows-1251")
    assert not declared_encoding_lies("Привет, мир".encode("utf-8") + b"\xd0", "latin-1")   # битых слишком много


def test_sync_and_genre_write_file_with_broken_byte(tmp_path):
    path = _with_broken_byte(tmp_path)
    assert _service()._patch_fb2_tags(path, "Артемьев Роман", "", "Отец Народа") is True
    assert GenreAssignmentService()._assign_genre_to_file(path, "Фантастика") is True
    text = path.read_bytes().decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<last-name>Артемьев</last-name>" in text and "Отец Народа" in text
    assert "<genre>Фантастика</genre>" in text and text.count("Этот мир безумен.") == 199


def test_patch_tags_drops_false_series(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.replace("latin-1", "utf-8").replace(
        "<book-title>", '<sequence name="Алешковский Петр"/><book-title>').format(text="Текст").encode("utf-8"))
    assert _service()._patch_fb2_tags(path, None, "", None, drop_series=True) is True
    assert "<sequence" not in path.read_text(encoding="utf-8")


def test_utf16_file_gets_tags_and_genre(tmp_path):
    """«Ивакин Алексей, Ким Сергей - Флешка» — единственная книга библиотеки в
    UTF-16: декларация в байтах не находилась, теги и жанр не записывались."""
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.replace("latin-1", "utf-16").format(text="Флешка").encode("utf-16"))
    assert _service()._patch_fb2_tags(path, None, "", "Флешка") is True
    assert GenreAssignmentService()._assign_genre_to_file(path, "Военная проза") is True
    text = path.read_bytes().decode("utf-16")
    assert "<book-title>Флешка</book-title>" in text and "<genre>Военная проза</genre>" in text
