"""Допуск папки к синхронизации (fb2parser_core.sync_eligibility): все
FB2-файлы на любой глубине вложенности размечены жанрами дерева — значения
`<genre>` совпадают с именами узлов дерева любого уровня. Сырой FB2-код
(`sf_social`), пустой `<genre>` или любой файл вне правила закрывают всю
папку; пустая папка не допускается."""
import io
import os
import zipfile

from fb2parser_core.sync_eligibility import check_folder, file_genres

TREE = ["Фантастика", "Космическая фантастика", "Детектив"]  # подветка тоже считается

_FB2 = """<?xml version="1.0" encoding="{enc}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>{genres}
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>Книга</book-title></title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _book(path, *genres, enc="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = _FB2.format(enc=enc, genres="".join(f"<genre>{g}</genre>" for g in genres))
    data = xml.encode(enc)
    if path.name.endswith(".fb2.zip"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(path.name[:-4], data)
        data = buf.getvalue()
    path.write_bytes(data)
    return path


def test_all_files_with_tree_genres_at_any_depth_are_eligible(tmp_path):
    _book(tmp_path / "А" / "1.fb2", "Фантастика")
    _book(tmp_path / "А" / "Серия" / "Глубже" / "2.fb2", "Космическая фантастика", "Детектив")
    _book(tmp_path / "Б" / "3.fb2.zip", "Детектив")
    _book(tmp_path / "Б" / "4.fb2", "Фантастика", enc="windows-1251")

    check = check_folder(str(tmp_path), TREE)

    assert check.eligible and check.total == 4 and check.bad_files == []
    assert check.genres == ["Детектив", "Фантастика", "Космическая фантастика"]  # по числу книг


def test_one_file_outside_the_rule_blocks_the_whole_folder(tmp_path):
    _book(tmp_path / "1.fb2", "Фантастика")
    _book(tmp_path / "Вложенная" / "2.fb2", "Фантастика", "sf_social")  # сырой код рядом с жанром дерева
    _book(tmp_path / "Вложенная" / "3.fb2")  # без <genre>

    check = check_folder(str(tmp_path), TREE)

    assert not check.eligible and check.total == 3
    assert sorted(check.bad_files) == [os.path.join("Вложенная", "2.fb2"), os.path.join("Вложенная", "3.fb2")]


def test_empty_or_missing_folder_is_not_eligible(tmp_path):
    (tmp_path / "пусто").mkdir()
    assert not check_folder(str(tmp_path / "пусто"), TREE).eligible
    assert not check_folder(str(tmp_path / "нет"), TREE).eligible


def test_file_genres_reads_zip_and_single_byte_encodings(tmp_path):
    assert file_genres(_book(tmp_path / "a.fb2.zip", "Детектив")) == ["Детектив"]
    assert file_genres(_book(tmp_path / "b.fb2", "Фантастика", enc="windows-1251")) == ["Фантастика"]
    assert file_genres(_book(tmp_path / "c.fb2")) == []
