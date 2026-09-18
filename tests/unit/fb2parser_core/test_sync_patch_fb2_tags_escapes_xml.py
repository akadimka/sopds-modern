"""Регрессия для `SynchronizationService._patch_fb2_tags()` —
docs/quality-roadmap.md, баг №85.

Найдено при архитектурном аудите: `_patch_fb2_tags()` вставляет
`proposed_author`/`proposed_series` в XML файла напрямую через f-строки,
БЕЗ экранирования — в отличие от соседнего патча `<book-title>`, который
уже вызывает `html.escape()`. Оба значения приходят из эвристического
пайплайна (сырые метаданные/имена папок), а не из уже провалидированных
для путей переменных, и могут содержать `&`, `<` или `"`. Итог — файл,
который синхронизация уже ПЕРЕНЕСЛА в библиотеку, перезаписывается
невалидным XML, без какого-либо отката.
"""
from pathlib import Path

import pytest
from lxml import etree

from fb2parser_core.logger import Logger
from fb2parser_core.synchronization import SynchronizationService

FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>prose</genre>
<author><first-name>Старый</first-name><last-name>Автор</last-name></author>
<book-title>Старое название</book-title>
<sequence name="Старая серия" number="1"/>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _make_service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.logger = Logger()
    svc.log_callback = None
    svc.db_path = tmp_path / "nonexistent.db"
    svc.stats = {}
    return svc


def _write_fb2(tmp_path, name="book.fb2"):
    path = tmp_path / name
    path.write_text(FB2_TEMPLATE, encoding="utf-8")
    return path


class TestPatchFb2TagsEscapesSpecialCharacters:
    def test_series_with_ampersand_produces_valid_xml(self, tmp_path):
        svc = _make_service(tmp_path)
        fb2_path = _write_fb2(tmp_path)

        svc._patch_fb2_tags(
            fb2_path,
            proposed_author=None,
            proposed_series="Мир & Хаос",
            proposed_title=None,
        )

        raw = fb2_path.read_bytes()
        # Без экранирования '&' в "Мир & Хаос" файл невозможно распарсить
        # как XML — lxml бросает XMLSyntaxError.
        root = etree.fromstring(raw)
        seq = root.find(".//{http://www.gribuser.ru/xml/fictionbook/2.0}sequence")
        assert seq is not None
        assert seq.get("name") == "Мир & Хаос"

    def test_author_lastname_with_ampersand_produces_valid_xml(self, tmp_path):
        svc = _make_service(tmp_path)
        fb2_path = _write_fb2(tmp_path)

        svc._patch_fb2_tags(
            fb2_path,
            proposed_author="Роджерс&Хаммерстайн Джон",
            proposed_series="",
            proposed_title=None,
        )

        raw = fb2_path.read_bytes()
        root = etree.fromstring(raw)
        ns = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
        last_name = root.find(f".//{ns}author/{ns}last-name")
        assert last_name is not None
        assert last_name.text == "Роджерс&Хаммерстайн"

    def test_series_with_quote_and_angle_bracket_produces_valid_xml(self, tmp_path):
        svc = _make_service(tmp_path)
        fb2_path = _write_fb2(tmp_path)

        svc._patch_fb2_tags(
            fb2_path,
            proposed_author=None,
            proposed_series='Серия "Икс" <проверка>',
            proposed_title=None,
        )

        raw = fb2_path.read_bytes()
        root = etree.fromstring(raw)
        seq = root.find(".//{http://www.gribuser.ru/xml/fictionbook/2.0}sequence")
        assert seq is not None
        assert seq.get("name") == 'Серия "Икс" <проверка>'

    def test_clean_values_still_written_correctly(self, tmp_path):
        # Контроль: обычные значения без спецсимволов патчатся как раньше.
        svc = _make_service(tmp_path)
        fb2_path = _write_fb2(tmp_path)

        svc._patch_fb2_tags(
            fb2_path,
            proposed_author="Иванов Пётр",
            proposed_series="Новая серия",
            proposed_title="Новое название",
        )

        raw = fb2_path.read_bytes()
        root = etree.fromstring(raw)
        ns = "{http://www.gribuser.ru/xml/fictionbook/2.0}"
        assert root.find(f".//{ns}author/{ns}last-name").text == "Иванов"
        assert root.find(f".//{ns}author/{ns}first-name").text == "Пётр"
        assert root.find(f".//{ns}sequence").get("name") == "Новая серия"
        assert root.find(f".//{ns}book-title").text == "Новое название"
