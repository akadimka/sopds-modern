# import PythonMagick
# from PIL import Image, ImageFile
import re

from lxml import etree

strip_symbols = " »«'\"&\n-.#\\`"


def safe_xml_parser(**kwargs):
    """lxml `XMLParser`, защищённый от XXE.

    Баг №94: lxml по умолчанию резолвит `<!ENTITY ...>`-декларации из
    `<!DOCTYPE>` (`resolve_entities=True`) — для файлов, которые могут
    быть скачаны откуда угодно (FB2/EPUB), это классический XXE:
    `<!ENTITY x SYSTEM "file:///etc/passwd">` + `&x;` где-нибудь в
    title/annotation даёт локальное чтение файлов, результат которого
    попадает в БД каталога и отдаётся всем читателям. Использовать для
    ЛЮБОГО парсинга содержимого книги, не только "надёжных" файлов.
    """
    kwargs.setdefault("resolve_entities", False)
    kwargs.setdefault("no_network", True)
    return etree.XMLParser(**kwargs)


def list_zip_file_infos(zipfile):
    return [info for info in zipfile.infolist() if not info.filename.endswith("/")]


def normalize_string(text: str) -> str | None:
    """Убирает повторяющиеся пробелы и переносы строки во входной строке"""
    if text is None:
        return None
    return re.sub(r"\s+", " ", text.strip())


