import logging
import os
from io import BytesIO

from book_tools.format.bookfile import BookFile
from book_tools.format.mimetype import Mimetype
from book_tools.format.util import list_zip_file_infos
from book_tools.mime_detector import detect_mime_service
from book_tools.services import create_bookfile_service

logger = logging.getLogger(__name__)


class mime_detector:
    @staticmethod
    def fmt(fmt):
        if fmt.lower() == "xml":
            return Mimetype.XML
        elif fmt.lower() == "fb2":
            return Mimetype.FB2
        elif fmt.lower() == "epub":
            return Mimetype.EPUB
        elif fmt.lower() == "mobi":
            return Mimetype.MOBI
        elif fmt.lower() == "zip":
            return Mimetype.ZIP
        elif fmt.lower() == "pdf":
            return Mimetype.PDF
        # elif fmt.lower() == "doc" or fmt.lower() == "docx":
        elif fmt.lower() in ("doc", "docx"):
            return Mimetype.MSWORD
        elif fmt.lower() == "djvu":
            return Mimetype.DJVU
        elif fmt.lower() == "txt":
            return Mimetype.TEXT
        elif fmt.lower() == "rtf":
            return Mimetype.RTF
        # else:
        return Mimetype.OCTET_STREAM

    @staticmethod
    def file(filename):
        (n, e) = os.path.splitext(filename)
        return mime_detector.fmt(e[1:])




def create_bookfile(path, original_filename=None) -> BookFile:
    """Извлечение метаданных электронной книги.

    Args:
        path: Путь к файлу (строка) или file-like объект (BytesIO).
        original_filename: Имя файла (обязательно если path — file-like).

    Returns:
        BookFile: извлеченные метаданные книги.
    """
    if isinstance(path, str):
        # If path is a string, open fd and pass raw (без BytesIO)
        if original_filename is None:
            original_filename = os.path.basename(path)
        logger.info(f"Read {original_filename} content from file system")
        fd = open(path, "rb")
        try:
            return create_bookfile_service(BytesIO(fd.read()), original_filename)
        finally:
            fd.close()
    else:
        # File-like object (BytesIO) — pass through directly
        if original_filename is None:
            raise ValueError("original_filename is required for file-like objects")
        data = BytesIO(path.read())
        return create_bookfile_service(data, original_filename)


