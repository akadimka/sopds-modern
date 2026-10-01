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


# Лимиты распаковки (защита от zip-бомб: архив в килобайты, внутри — гигабайты,
# которые читаются целиком в память воркера/сканера). zipfile не отдаёт больше
# объявленного ZipInfo.file_size, поэтому проверки объявленного размера достаточно.
MAX_FB2_XML_SIZE = 200 * 1024 * 1024    # то же, что fb2_utils.MAX_FB2_UNCOMPRESSED_SIZE
MAX_BOOK_FILE_SIZE = 1024 * 1024 * 1024  # книга из zip-коллекции (выдача, обложка)
MAX_COVER_SIZE = 50 * 1024 * 1024


class ZipMemberTooLarge(ValueError):
    pass


def check_zip_member_size(info, max_size: int) -> None:
    if info.file_size > max_size:
        raise ZipMemberTooLarge(
            f"'{info.filename}' declares {info.file_size} bytes uncompressed "
            f"(> {max_size}) - refusing, looks like a zip bomb"
        )


def read_zip_member(zf, member, max_size: int) -> bytes:
    """Прочитать член архива целиком, если его объявленный размер в пределах лимита."""
    info = member if hasattr(member, "file_size") else zf.getinfo(member)
    check_zip_member_size(info, max_size)
    return zf.read(info)


def list_zip_file_infos(zipfile):
    return [info for info in zipfile.infolist() if not info.filename.endswith("/")]


def normalize_string(text: str) -> str | None:
    """Убирает повторяющиеся пробелы и переносы строки во входной строке"""
    if text is None:
        return None
    return re.sub(r"\s+", " ", text.strip())


