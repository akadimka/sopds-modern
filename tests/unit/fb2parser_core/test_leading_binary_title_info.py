"""FB2 с <binary> перед <description> (баг №136).

Реальный случай: «Нанана-Нана - Покорять подземелье с красоткой-начальницей —
это сверхурочные\\01. Том 1.fb2» — обложка 180 КБ стоит в начале файла,
<title-info> на 181-м КБ. Метаданные и жанр читались из первых 64 КБ, поэтому
не находились: смена жанра записывалась в файл, но прочитать её обратно
(и название, и авторов) было нельзя — «жанр не меняется».
"""
import zipfile

import pytest

from fb2parser_core.fb2_author_extractor import FB2AuthorExtractor
from fb2parser_core.fb2_utils import read_fb2_head
from fb2parser_core.genre_assign import GenreAssignmentService
from fb2parser_core.sync_eligibility import file_genres

_COVER = "A" * 200_000  # base64-обложка больше 64 КБ

_FB2 = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><binary id="poster.jpg" content-type="image/jpeg">{_COVER}</binary>
<description>
<title-info>
<genre>Фантастика</genre>
<author><first-name>Nana</first-name><last-name>Nanana</last-name></author>
<book-title>Attacking dungeon</book-title>
</title-info>
</description>
<body><section><p>Текст книги.</p></section></body>
</FictionBook>
"""


@pytest.fixture
def fb2(tmp_path):
    path = tmp_path / "01. Том 1.fb2"
    path.write_text(_FB2, encoding="utf-8")
    return path


@pytest.fixture
def extractor():
    return FB2AuthorExtractor()


def test_head_reaches_title_info(fb2):
    assert b"</title-info>" in read_fb2_head(fb2)


def test_head_of_zipped_fb2(tmp_path):
    path = tmp_path / "book.fb2.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("book.fb2", _FB2)
    assert b"</title-info>" in read_fb2_head(path)


def test_metadata_read(fb2, extractor):
    meta = extractor._extract_all_metadata_at_once(fb2)
    assert meta["title"] == "Attacking dungeon"
    assert meta["genre"] == "Фантастика"
    assert "Nanana" in meta["authors"]


def test_genre_change_is_read_back(fb2, extractor):
    assert GenreAssignmentService()._assign_genre_to_file(fb2, "Приключения")
    assert extractor._extract_genres_from_fb2(fb2) == "Приключения"
    assert file_genres(fb2) == ["Приключения"]
