"""Регрессия для `fb2parser_web.views._classify_broken_or_incomplete()` —
поддержка кнопки «Битые/неполные файлы» (docs/quality-roadmap.md).

Реальный случай (замечен пользователем): несколько книг в библиотеке —
ознакомительные фрагменты ("Шопперт Андрей - И опять Пожарский
(Гепталогия).fb2" — 6-й из 7 томов внутри обрезан на фразе "Конец
ознакомительного фрагмента"), а не полный текст. Ни один существующий
проход (regen_csv/fb2_compiler) не проверяет содержимое файла на этот
признак — такие файлы молча компилировались и синхронизировались как
полноценные тома. Кнопка "Битые файлы" в normalize.html существовала как
заготовка ("Stage 2", disabled, без backend) — реализована здесь вместе
с проверкой на ознакомительные фрагменты.
"""
from pathlib import Path

import django
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sopds.settings.local")
django.setup()

from fb2parser_web.views import _classify_broken_or_incomplete  # noqa: E402

_VALID_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>Название</book-title></title-info></description>
<body><title><p>Название</p></title><section><p>Текст книги.</p></section></body>
</FictionBook>
"""

_FRAGMENT_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>Название</book-title></title-info></description>
<body><title><p>Название</p></title>
<section><p>Начало книги...</p><p>Конец ознакомительного фрагмента.</p></section></body>
</FictionBook>
"""


class TestClassifyBrokenOrIncomplete:
    def test_valid_fb2_returns_none(self, tmp_path):
        p = tmp_path / "valid.fb2"
        p.write_text(_VALID_FB2, encoding="utf-8")
        assert _classify_broken_or_incomplete(p) is None

    def test_preview_fragment_marker_detected(self, tmp_path):
        p = tmp_path / "fragment.fb2"
        p.write_text(_FRAGMENT_FB2, encoding="utf-8")
        assert _classify_broken_or_incomplete(p) == "incomplete"

    def test_unparseable_content_detected_as_broken(self, tmp_path):
        p = tmp_path / "broken.fb2"
        p.write_bytes(b"\x00\x01not even xml\xff\xfe")
        assert _classify_broken_or_incomplete(p) == "broken"

    def test_missing_file_detected_as_broken(self, tmp_path):
        p = tmp_path / "does_not_exist.fb2"
        assert _classify_broken_or_incomplete(p) == "broken"
