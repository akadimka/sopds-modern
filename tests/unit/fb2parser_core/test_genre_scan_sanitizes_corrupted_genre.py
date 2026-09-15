"""Регрессия для `scan_fb2_genres()` — docs/quality-roadmap.md, баг №75.

Реальный случай (замечен пользователем в панели "Genres" сканирования
папки перед синхронизацией): жанр в метаданных нескольких старых FB2-
файлов испорчен на уровне ИСХОДНОГО файла — `<genre>??????????</genre>`
(старый конвертер не смог записать кириллицу и подставил заполнитель,
тот же артефакт, что и в баге №44, только здесь — не в самой
синхронизации, а в отдельной, независимой панели предварительного
сканирования жанров, где такой очистки не было вовсе). Список показывал
нечитаемые "жанры" вроде "???????": 1 и "??????????": 6 как будто это
настоящие, отдельные категории.
"""
from pathlib import Path

from fb2parser_core.genre_scan_service import scan_fb2_genres
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>{genre}</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _write_fb2(path: Path, genre: str):
    path.write_text(_FB2_TEMPLATE.format(genre=genre), encoding="utf-8")


class TestGenreScanSanitizesCorruptedGenre:
    def test_corrupted_genre_grouped_as_undetermined(self, tmp_path):
        _write_fb2(tmp_path / "corrupted1.fb2", "??????????")
        _write_fb2(tmp_path / "corrupted2.fb2", "???????")
        _write_fb2(tmp_path / "clean.fb2", "детектив")

        result = scan_fb2_genres(tmp_path, _config_path())

        assert "??????????" not in result["results"]
        assert "???????" not in result["results"]
        assert len(result["results"].get("Не определено", [])) == 2
        assert result["results"]["детектив"] == ["clean.fb2"]

    def test_clean_genre_untouched(self, tmp_path):
        _write_fb2(tmp_path / "clean.fb2", "классическая проза")

        result = scan_fb2_genres(tmp_path, _config_path())

        assert result["results"] == {"классическая проза": ["clean.fb2"]}
