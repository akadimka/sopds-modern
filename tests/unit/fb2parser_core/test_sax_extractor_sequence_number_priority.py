"""Регрессия для `FB2SAXExtractor` — docs/quality-roadmap.md, баг №81.

Реальный случай (Зан Тимоти, "Траун. Доминация", FANZON): у книги 2 в
`<title-info>` идут `<sequence number="2" name="Траун. Доминация"/>`
ПЕРВЫМ, затем `<sequence name="Звёздные Войны"/>` (родовая франшиза, без
номера) — а у книги 3 порядок ОБРАТНЫЙ: `<sequence name="Звёздные
войны"/>` первым, `<sequence name="Траун. Доминация" number="3"/>`
вторым. Старое правило "последний тег в файле побеждает" (чистая
случайность порядка) давало РАЗНУЮ `metadata_series` для соседних томов
одной и той же серии — "Звёздные Войны" для книги 2, но правильную
"Траун. Доминация" для книги 3.
"""
from pathlib import Path

from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author><first-name>Тимоти</first-name><last-name>Зан</last-name></author>
<book-title>Тест</book-title>
{sequences}
</title-info>
</description>
<body><section><p>Text</p></section></body>
</FictionBook>
"""


def _write_fb2(tmp_path: Path, sequences: str) -> Path:
    p = tmp_path / "book.fb2"
    p.write_text(_FB2_TEMPLATE.format(sequences=sequences), encoding="utf-8")
    return p


class TestNumberedSequenceTakesPriorityOverOrder:
    def test_numbered_series_first_franchise_second(self, tmp_path):
        path = _write_fb2(
            tmp_path,
            '<sequence number="2" name="Траун. Доминация"/>'
            '<sequence name="Звёздные Войны"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Траун. Доминация"
        assert meta["series_number"] == "2"

    def test_franchise_first_numbered_series_second(self, tmp_path):
        path = _write_fb2(
            tmp_path,
            '<sequence name="Звёздные войны"/>'
            '<sequence number="3" name="Траун. Доминация"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Траун. Доминация"
        assert meta["series_number"] == "3"

    def test_no_number_anywhere_keeps_first_tag(self, tmp_path):
        # Sanity: если НИ у одного тега нет номера, побеждает первый —
        # не должно измениться поведение для книг без явной серии.
        path = _write_fb2(
            tmp_path,
            '<sequence name="Звёздные войны"/>'
            '<sequence name="Легенды"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Звёздные войны"
