"""Регрессия — docs/quality-roadmap.md, баг №94.

Найдено при архитектурном аудите: ни один вызов `etree.parse`/
`etree.fromstring`/`etree.XMLParser` в `book_tools.format` (парсеры
FB2/EPUB, обрабатывающие файлы, которые могут быть скачаны откуда
угодно) не запрещал резолвинг XML-сущностей (`resolve_entities`) —
lxml по умолчанию резолвит `<!ENTITY ...>`, объявленные во внутреннем
DOCTYPE-подмножестве самого документа, в текст, куда бы они ни попали
(title/author/annotation/...). Реально воспроизводится ПРОСТОЙ
внутренней сущностью (не требует внешнего SYSTEM-ресурса и load_dtd) —
подтверждено вручную: `etree.XMLParser(recover=True)` (старый код)
резолвит `&hello;` в буквальный текст, а `safe_xml_parser()` (фикс) —
не резолвит вовсе (сущность остаётся нераскрытой). Это открывает
и content-спуфинг через метаданные, и DoS через вложенные сущности
("billion laughs").
"""
import zipfile
from io import BytesIO

from lxml import etree

from book_tools.format.epub import EPub
from book_tools.format.fb2 import FB2 as LegacyFB2
from book_tools.format.parsers import EpubParser
from book_tools.format.parsers import FB2 as ParsersFB2
from book_tools.format.util import safe_xml_parser

ENTITY_MARKER = "INTERNAL-SUBSTITUTED-TEXT"


def _xxe_fb2_bytes(marker=ENTITY_MARKER) -> bytes:
    return (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE FictionBook [<!ENTITY xxe "{marker}">]>'
        "<FictionBook><description><title-info>"
        "<book-title>&xxe;</book-title>"
        "<author><first-name>A</first-name><last-name>B</last-name></author>"
        "</title-info></description>"
        "<body><section><p>x</p></section></body></FictionBook>"
    ).encode("utf-8")


def _build_malicious_epub_zip(marker=ENTITY_MARKER) -> BytesIO:
    opf = (
        '<?xml version="1.0"?>'
        f'<!DOCTYPE package [<!ENTITY xxe "{marker}">]>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>&xxe;</dc:title>"
        "</metadata>"
        "<manifest/><spine/>"
        "</package>"
    ).encode("utf-8")
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        "<rootfiles>"
        '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles>"
        "</container>"
    ).encode("utf-8")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        zf.writestr(mimetype_info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
    buf.seek(0)
    return buf


class TestSafeXmlParserUnit:
    def test_blocks_internal_entity_substitution(self):
        xml = _xxe_fb2_bytes()
        tree = etree.parse(BytesIO(xml), safe_xml_parser(recover=True))
        node = tree.find(".//book-title")
        assert node is not None
        assert node.text != ENTITY_MARKER

    def test_unhardened_parser_would_have_leaked_it(self):
        """Контроль: доказываем, что уязвимость реальна для СТАРОГО
        кода (`etree.XMLParser(recover=True)` без resolve_entities),
        а не выдумана — иначе тест выше ничего бы не доказывал."""
        xml = _xxe_fb2_bytes()
        tree = etree.parse(BytesIO(xml), etree.XMLParser(recover=True))
        node = tree.find(".//book-title")
        assert node is not None
        assert node.text == ENTITY_MARKER


class TestFb2ParsersDoNotResolveEntities:
    def test_parsers_fb2_title_not_substituted(self):
        book = ParsersFB2(BytesIO(_xxe_fb2_bytes()))
        assert book.title != ENTITY_MARKER

    def test_legacy_fb2_title_not_substituted(self):
        book = LegacyFB2(BytesIO(_xxe_fb2_bytes()), "malicious.fb2")
        assert book.title != ENTITY_MARKER


class TestEpubParsersDoNotResolveEntities:
    def test_epub_parser_title_not_substituted(self):
        parser = EpubParser(_build_malicious_epub_zip())
        parser.parse()
        assert parser.title != ENTITY_MARKER

    def test_epub_old_title_not_substituted(self):
        epub = EPub(_build_malicious_epub_zip(), "malicious.epub")
        assert epub.title != ENTITY_MARKER
