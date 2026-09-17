"""Регрессия для `FB2AuthorExtractor._extract_genres_from_fb2()`.

Обнаружено пользователем: сканирование жанров по всей библиотеке (панель
"Genre Combinations" / кнопка "Scan" на Home) работало очень медленно.
Причина — `_extract_genres_from_fb2()` вызывал `_detect_correct_encoding()`
БЕЗ лимита `max_bytes`, а значит читал и декодировал файл ЦЕЛИКОМ ради
тега <genre>, лежащего в первых байтах <title-info>. На компиляциях
(5-20+ МБ за счёт встроенных обложек/иллюстраций в <body>/<binary>) это
означало полное чтение каждого такого файла, один за другим (сканирование
не многопоточное) — основная причина медленной работы на реальной
библиотеке.

`_extract_all_metadata_at_once()` уже решает ту же задачу правильно
(`max_bytes=65536` — see fb2_author_extractor.py:1180-1182) — этот тест
закрепляет то же поведение для `_extract_genres_from_fb2()`.
"""
from pathlib import Path

from fb2parser_core.fb2_author_extractor import FB2AuthorExtractor
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>детектив</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>{padding}</p></section></body>
</FictionBook>
"""


def _write_large_fb2(path: Path, padding_bytes: int) -> None:
    padding = "А" * padding_bytes
    path.write_text(_FB2_TEMPLATE.format(padding=padding), encoding="utf-8")


class TestGenreExtractionBoundsReadSize:
    def test_genre_found_despite_multi_megabyte_body(self, tmp_path):
        fb2_path = tmp_path / "big_compilation.fb2"
        _write_large_fb2(fb2_path, padding_bytes=8 * 1024 * 1024)

        extractor = FB2AuthorExtractor(_config_path())
        assert extractor._extract_genres_from_fb2(fb2_path) == "детектив"

    def test_detect_correct_encoding_called_with_bounded_max_bytes(self, tmp_path, monkeypatch):
        fb2_path = tmp_path / "small.fb2"
        _write_large_fb2(fb2_path, padding_bytes=100)

        extractor = FB2AuthorExtractor(_config_path())
        captured = {}
        original = FB2AuthorExtractor._detect_correct_encoding

        def _spy(self, path, max_bytes=0):
            captured["max_bytes"] = max_bytes
            return original(self, path, max_bytes=max_bytes)

        monkeypatch.setattr(FB2AuthorExtractor, "_detect_correct_encoding", _spy)
        extractor._extract_genres_from_fb2(fb2_path)

        assert captured["max_bytes"] == 65536
