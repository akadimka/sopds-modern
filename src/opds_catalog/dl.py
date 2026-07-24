# -*- coding: utf-8 -*-
import codecs
import importlib.util
import io
import json
import logging
import os
import subprocess
import zipfile

from opds_catalog.sopds_config import sopds_cfg as config
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    HttpResponseRedirect,
    JsonResponse,
)
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from PIL import Image

from book_tools.format import create_bookfile, mime_detector
from book_tools.format.mimetype import Mimetype
from book_tools.format.parsers import FB2
from opds_catalog import opdsdb, settings, utils
from opds_catalog.decorators import sopds_auth_validate
from opds_catalog.models import Book, bookshelf
from opds_catalog.utils import getFileData, getFileName

logger = logging.getLogger(__name__)
SOPDS_DEFAULT_COVER = "/static/images/sopds-ng-nocover.png"


@sopds_auth_validate
def Download(request, book_id, zip_flag):
    # TODO: это view, он должен быть в другом месте
    # TODO: реорганизовать в части формирования ответа
    """Загрузка файла книги"""
    logger.info(f"Processing request book {book_id}for download")
    logger.debug(f"Download {book_id}")
    logger.debug(f"Zip flag: {zip_flag}")
    logger.info(f"Reading book {book_id} metadata from database")
    book = Book.objects.get(id=book_id)

    logger.info("Processing user bookshelf ")
    if config.SOPDS_AUTH:
        if request.user.is_authenticated:
            bookshelf.objects.get_or_create(user=request.user, book=book)

    logger.info("Prepare book filename and content type")
    from urllib.parse import quote
    dlfilename = getFileName(book)
    if zip_flag == "1":
        dlfilename = dlfilename + ".zip"
        content_type = Mimetype.FB2_ZIP if book.format == "fb2" else Mimetype.ZIP
    else:
        content_type = mime_detector.fmt(book.format)

    logger.debug(f"Filename: {dlfilename}")
    logger.debug(f"Content type: {content_type}")

    # RFC 5987: filename*=UTF-8''<percent-encoded> — поддерживается всеми современными браузерами
    encoded_name = quote(dlfilename, safe='')
    ascii_name = dlfilename.encode('ascii', 'replace').decode()

    response = HttpResponse()
    response["Content-Type"] = content_type
    response["Content-Disposition"] = (
        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
    )
    response["Content-Transfer-Encoding"] = "binary"

    s = getFileData(book)
    if s is None:
        # Книга не может быть прочитана из файловой системы, подробности зафиксированы в логе.
        # TODO: Сделать нормальную обработку и вернуть нормальную страницу
        return HttpResponseNotFound(
            f"Book {book.id} with title '{book.title}' was not found in library files"
        )

    if zip_flag == "1":
        logger.info("Packing content to ZIP")
        dio = io.BytesIO()
        with zipfile.ZipFile(dio, "w", zipfile.ZIP_DEFLATED) as zo:
            zo.writestr(getFileName(book), s.getvalue())

        response["Content-Length"] = str(dio.getbuffer().nbytes)
        response.write(dio.getvalue())
    else:
        response["Content-Length"] = str(s.getbuffer().nbytes)
        response.write(s.getvalue())

    return response


# Новая версия (0.42) процедуры извлечения обложек из файлов книг fb2, epub, mobi
# @cache_page(config.SOPDS_CACHE_TIME)
def Cover(
    request: HttpRequest, book_id: int, thumbnail=False
) -> HttpResponse | HttpResponseRedirect:
    # FIXME: Это view, он должен находиться в другом файле
    """
    Загрузка обложки

    Args:
        request(HttpRequest): поступивший django запрос

        book_id(int): идентификатор книги

        thumbnail(bool): требуется ли создавать превью обложки

    Returns:
       HttpResponse: изображение обложки, если обложка была найдена в книге
       HttpResponseRedirect: ссылка на стандартную обложку, если обложка не бла найдена в книге
    """
    logger.info(f"Reading book cover for book_id {book_id}")
    book = Book.objects.get(id=book_id)
    logger.info("Book meta loaded")
    logger.debug(f"Book title = {book.title}")
    response = HttpResponse()
    # full_path = get_fs_book_path(book)

    try:
        logger.info(f"Extract cover for book in {book.format} format")
        if book.format == "fb2":
            content = getFileData(book)
            assert content is not None
            parser = FB2(content)
            image = parser.extract_cover()
        else:
            logger.info("Extract cover from non-fb2 book")
            book_data = create_bookfile(getFileData(book), book.filename)
            image = book_data.extract_cover_memory()
    except Exception as e:
        logger.error(f"Error while extract cover from {book.title}: {e}")
        book_data = None
        image = None

    if image:
        logger.info("Cover extracted, creating response")
        response["Content-Type"] = "image/jpeg"
        if thumbnail:
            thumb = Image.open(io.BytesIO(image)).convert("RGB")
            thumb.thumbnail(
                (settings.THUMB_SIZE, settings.THUMB_SIZE), Image.Resampling.LANCZOS
            )
            tfile = io.BytesIO()
            thumb.save(tfile, "JPEG")
            image = tfile.getvalue()
        response.write(image)

    if not image:
        logger.info(f"Cover for book with id {book.id} is not found")
        # Вместо обработки изображения отдаем ссылку на изображение "Нет обложки"
        return HttpResponseRedirect(SOPDS_DEFAULT_COVER)

    return response


def Thumbnail(request, book_id):
    return Cover(request, book_id, True)


@xframe_options_exempt
def ViewHtml(request, book_id):
    """Отдать книгу как HTML для чтения в браузере (только fb2)."""
    from django.urls import reverse

    book = Book.objects.get(id=book_id)

    if book.format.lower() != "fb2":
        raise Http404

    fb2_data = getFileData(book)
    if fb2_data is None:
        raise Http404

    resume_anchor = ""
    if request.user.is_authenticated:
        entry = bookshelf.objects.filter(user=request.user, book=book).first()
        if entry:
            resume_anchor = entry.anchor_id

    convert_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "convert"))
    spec = importlib.util.spec_from_file_location(
        "fb2_to_html", os.path.join(convert_dir, "fb2_to_html.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    html_content = module.convert_bytes_to_html_string(
        fb2_data.read(),
        progress_url=reverse("opds:save_progress", args=[book_id]) if request.user.is_authenticated else "",
        resume_anchor=resume_anchor,
    )

    return HttpResponse(html_content, content_type="text/html; charset=utf-8")


@csrf_exempt
@require_POST
def SaveProgress(request, book_id):
    """Сохранить позицию чтения (вызывается JS из страницы ридера).

    CSRF-исключение осознанное: запрос шлётся и обычным fetch() во время
    чтения, и navigator.sendBeacon() при закрытии вкладки (единственный
    надёжный способ не потерять последнюю позицию) — а Beacon API не умеет
    добавлять кастомные заголовки, поэтому X-CSRFToken туда не воткнуть.
    Худшее, что может сделать CSRF здесь — испортить пользователю
    сохранённую позицию чтения; это не открывает доступ к чужим данным и
    не требует аутентификации для эксплуатации кем-то посторонним (только
    от лица уже залогиненного пользователя, чей браузер шлёт запрос).
    """
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "auth required"}, status=401)
    try:
        book = Book.objects.get(id=book_id)
    except Book.DoesNotExist:
        raise Http404
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    anchor_id = str(data.get("anchor_id", ""))[:64]
    try:
        percent = max(0.0, min(100.0, float(data.get("percent", 0))))
    except (TypeError, ValueError):
        percent = 0.0

    entry, _created = bookshelf.objects.get_or_create(user=request.user, book=book)
    entry.anchor_id = anchor_id
    entry.progress_percent = percent
    entry.readtime = timezone.now()
    if percent >= 98.0:
        entry.finished = True
    entry.save(update_fields=["anchor_id", "progress_percent", "readtime", "finished"])
    return JsonResponse({"ok": True})


def ConvertFB2(request, book_id, convert_type):
    """Выдача файла книги после конвертации в EPUB, MOBI или AZW3."""
    from urllib.parse import quote
    book = Book.objects.get(id=book_id)

    if book.format != "fb2":
        raise Http404

    if config.SOPDS_AUTH and request.user.is_authenticated:
        bookshelf.objects.get_or_create(user=request.user, book=book)

    full_path = os.path.join(config.SOPDS_ROOT_LIB, book.path)
    if book.cat_type == opdsdb.CAT_INP:
        # Убираем из пути INPX и INП файл
        inp_path, zip_name = os.path.split(full_path)
        inpx_path, inp_name = os.path.split(inp_path)
        path, inpx_name = os.path.split(inpx_path)
        full_path = os.path.join(path, zip_name)

    base_name = os.path.splitext(getFileName(book))[0]
    dlfilename = f"{base_name}.{convert_type}"

    if convert_type == "epub":
        converter_path = config.SOPDS_FB2TOEPUB
    elif convert_type == "mobi":
        converter_path = config.SOPDS_FB2TOMOBI
    elif convert_type == "azw3":
        converter_path = config.SOPDS_FB2TOAZW3
    else:
        raise Http404
    if not converter_path:
        raise Http404

    if not config.SOPDS_TEMP_DIR:
        raise Http404

    content_type = mime_detector.fmt(convert_type)

    if book.cat_type == opdsdb.CAT_NORMAL:
        tmp_fb2_path = None
        file_path = os.path.join(full_path, book.filename)
    elif book.cat_type in [opdsdb.CAT_ZIP, opdsdb.CAT_INP]:
        # FIXME: Исправить работу c codecs
        try:
            fz = codecs.open(full_path, "rb")
        except FileNotFoundError:
            raise Http404
        z = zipfile.ZipFile(fz, "r", allowZip64=True)
        z.extract(book.filename, config.SOPDS_TEMP_DIR)
        tmp_fb2_path = os.path.join(config.SOPDS_TEMP_DIR, book.filename)
        file_path = tmp_fb2_path

    tmp_conv_path = os.path.join(config.SOPDS_TEMP_DIR, dlfilename)
    proc = subprocess.Popen(
        [converter_path, file_path, tmp_conv_path],
        stdout=subprocess.PIPE,
    )
    proc.stdout.read()
    proc.wait()

    if os.path.isfile(tmp_conv_path):
        fo = codecs.open(tmp_conv_path, "rb")
        s = fo.read()
        encoded_name = quote(dlfilename, safe='')
        ascii_name = dlfilename.encode('ascii', 'replace').decode()
        response = HttpResponse()
        response["Content-Type"] = content_type
        response["Content-Disposition"] = (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
        )
        response["Content-Transfer-Encoding"] = "binary"
        response["Content-Length"] = str(len(s))
        response.write(s)
        fo.close()
    else:
        raise Http404

    try:
        if tmp_fb2_path:
            os.remove(tmp_fb2_path)
    except:
        pass
    try:
        os.remove(tmp_conv_path)
    except:
        pass

    return response
