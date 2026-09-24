"""Регрессия для `_run_genre_scan_thread()` — docs/quality-roadmap.md,
баг №114.

Реальный случай: пользователь хотел, чтобы кнопки сканирования жанров
("Scan genres" на Home и "Start scan" на Genre Combinations — обе
проходят через `_run_genre_scan_thread()`) сами пополняли справочник
FB2-кодов (`mygenres.json`) новыми, ранее неизвестными латинскими
кодами — а кириллические значения (ошибка/мусор метаданных, не код)
в справочник не заносить.
"""
import json

import pytest

from fb2parser_core.genres_manager import GenresManager
from fb2parser_web.views import _run_genre_scan_thread, genre_scan_job

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


def _write_fb2(path, genre):
    path.write_text(_FB2_TEMPLATE.format(genre=genre), encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    genre_scan_job.reset()
    genre_scan_job.finish()
    yield
    genre_scan_job.reset()
    genre_scan_job.finish()


class TestGenreScanRegistersDiscoveredCodes:
    def test_new_latin_code_lands_in_reference_skips_cyrillic(self, tmp_path, monkeypatch):
        folder = tmp_path / "books"
        folder.mkdir()
        _write_fb2(folder / "1.fb2", "brand_new_latin_code")
        _write_fb2(folder / "2.fb2", "Испорченный русский тег")

        reference_path = tmp_path / "mygenres.json"
        reference_path.write_text(json.dumps([
            {"model": "opds_catalog.genre", "pk": 1, "fields": {"genre": "sf_action", "section": "Фантастика", "subsection": ""}},
        ]), encoding="utf-8")
        genres_xml = tmp_path / "genres.xml"
        genres_xml.write_text(
            '<?xml version="1.0" encoding="utf-8"?><genres><genre name="Фантастика" /></genres>',
            encoding="utf-8",
        )
        gm = GenresManager(str(genres_xml), reference_path=str(reference_path))
        monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_genres_manager", lambda: gm)

        _run_genre_scan_thread([str(folder)])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        genres = [item["fields"]["genre"] for item in data]
        assert "brand_new_latin_code" in genres
        assert not any("рус" in g.lower() for g in genres)

        # Отображается в UI статуса скана ("Codes added to reference:").
        assert genre_scan_job.get()["discovered_codes_count"] == 1
