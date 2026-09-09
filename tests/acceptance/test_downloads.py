"""Тесты скачивания книг через HTTP — acceptance, с клиентом и БД."""

import base64
import os
from unittest.mock import patch

import pytest
from django.urls import reverse

from opds_catalog import opdsdb
from opds_catalog.models import Book

pytestmark = [pytest.mark.django_db, pytest.mark.acceptance]


# ── Downloads (скачивание книг через HTTP) ──────────────────────────────


@pytest.mark.usefixtures("fake_sopds_root_lib", "django_user", "load_db_data")
class TestDownloads:
    """Тесты endpoint'а скачивания книг."""

    @pytest.mark.override_config(SOPDS_AUTH=True)
    def test_unauthorized_downloads(self, client) -> None:
        response = client.get(reverse("opds:download", args=(5, 0)))
        assert response.status_code == 401

    @pytest.mark.override_config(SOPDS_AUTH=True)
    def test_authorized_download_book(self, client, django_user, test_rootlib) -> None:
        client.force_login(django_user)
        response = client.get(reverse("opds:download", args=(5, 0)))
        assert response.status_code == 200
        # Не хардкодим байт-размер: git на разных ОС может по-разному
        # переводить строки в текстовых fixture-файлах (LF/CRLF) — сравниваем
        # с РЕАЛЬНЫМ размером того же файла на диске в момент теста.
        expected_size = os.path.getsize(os.path.join(test_rootlib, "262001.fb2"))
        assert response["Content-Length"] == str(expected_size)

    @pytest.mark.override_config(SOPDS_AUTH=True)
    def test_basic_authentication(
        self, client, django_user, django_user_model, test_rootlib
    ) -> None:
        response = client.get(reverse("opds:download", args=(5, 0)))
        assert response.status_code == 401
        credentials = "test:secret"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        authorization_header = f"Basic {encoded_credentials}"
        response = client.get(
            reverse("opds:download", args=(5, 0)),
            HTTP_AUTHORIZATION=authorization_header,
        )
        assert response.status_code == 200
        expected_size = os.path.getsize(os.path.join(test_rootlib, "262001.fb2"))
        assert response["Content-Length"] == str(expected_size)

    @pytest.mark.override_config(SOPDS_AUTH=False)
    def test_download_zip(self, client) -> None:
        response = client.get(reverse("opds:download", args=(5, 1)))
        assert response.status_code == 200
        # Размер архива зависит от переводов строк в исходном текстовом
        # файле (см. test_authorized_download_book) — проверяем содержимое,
        # а не точный байт-размер упаковки.
        import zipfile
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            assert zf.namelist() == ["262001.fb2"]
            assert len(zf.read("262001.fb2")) > 0

    @pytest.mark.override_config(SOPDS_AUTH=False)
    def test_download_unexisted_book(self, client, unexisted_book) -> None:
        response = client.get(reverse("opds:download", args=(4, 0)))
        assert response.status_code == 404


# ── Обложки и thumbnail ──────────────────────────────────────────────────


@pytest.mark.parametrize("use_sax", [(True), (False)])
def test_get_book_cover(
    fake_sopds_root_lib, create_regular_book, client, override_config, use_sax
) -> None:
    """Обложка книги (FB2SAX вкл/выкл)."""
    book: Book = create_regular_book
    assert book is not None
    url = reverse("opds:cover", args=(book.id,))
    with override_config(SOPDS_FB2SAX=use_sax):
        actual = client.get(url)
        assert actual.status_code == 200
        assert actual["Content-Length"] == "56360"


def test_cover_redirect_when_no_cover(
    fake_sopds_root_lib,
    create_regular_book,
    client,
) -> None:
    """Cover без обложки -> редирект на заглушку."""
    book: Book = create_regular_book
    book.filename = "nonexist.fb2"
    book.save()
    url = reverse("opds:cover", args=(book.id,))
    response = client.get(url)
    assert response.status_code == 302
    assert "nocover" in response.url


def test_thumbnail(
    fake_sopds_root_lib, create_regular_book, client, override_config
) -> None:
    """Проверка Thumbnail."""
    book: Book = create_regular_book
    url = reverse("opds:thumb", args=(book.id,))
    with override_config(SOPDS_FB2SAX=True):
        response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"


# ── Вспомогательные тесты загрузок ───────────────────────────────────────


def test_wrong_encoded_fb2_zip(test_rootlib) -> None:
    """Чтение файла из ZIP архива с кодировкой, отличной от latin1(cp437)."""
    from opds_catalog.utils import read_from_zipped_file

    actual = read_from_zipped_file(
        os.path.join(test_rootlib, "wrong_encoded.zip"),
        "Носов - Незнайка-путешественник.fb2",
    )
    assert actual is not None


# ── Конвертация (EPUB/MOBI/AZW3) ─────────────────────────────────────────
#
# getFileDataConv/getFileDataEpub/getFileDataMobi были удалены — вся логика
# теперь инлайн в opds_catalog.dl.ConvertFB2 (view). Внешний конвертер
# (ebook-convert) в тестовом окружении недоступен, поэтому subprocess.Popen
# подменяется на копирование входного файла в выходной — это не проверяет
# сам конвертер (это отдельная внешняя зависимость), а проверяет НАШ код:
# какой файл ему передаётся на вход.


def _fake_converter(*args, **kwargs):
    """Подменяет ebook-convert: копирует вход в выход, как «успешная» конвертация."""
    import shutil
    from unittest.mock import MagicMock

    src, dst = args[0][1], args[0][2]
    shutil.copyfile(src, dst)
    proc = MagicMock()
    proc.stdout.read.return_value = b""
    proc.wait.return_value = 0
    return proc


@pytest.mark.usefixtures("fake_sopds_root_lib")
class TestConvertFB2:
    """Тесты view ConvertFB2 — конвертация в EPUB/MOBI/AZW3."""

    def test_convert_non_fb2_book_404(self, client, catalog) -> None:
        book = Book.objects.create(
            title="Not a fb2 book", search_title="NOT A FB2 BOOK",
            format="pdf", filename="x.pdf",
            path=".", cat_type=0, catalog=catalog,
        )
        response = client.get(reverse("opds:convert", args=(book.id, "epub")))
        assert response.status_code == 404

    def test_convert_no_converter_configured_404(
        self, client, create_regular_book, override_config
    ) -> None:
        with override_config(SOPDS_FB2TOEPUB="", SOPDS_TEMP_DIR="/tmp"):
            response = client.get(
                reverse("opds:convert", args=(create_regular_book.id, "epub"))
            )
        assert response.status_code == 404

    def test_convert_regular_book(
        self, client, create_regular_book, override_config, tmp_path
    ) -> None:
        """CAT_NORMAL: конвертер получает путь напрямую к файлу в библиотеке."""
        with override_config(
            SOPDS_FB2TOEPUB="fake-converter", SOPDS_TEMP_DIR=str(tmp_path)
        ):
            with patch("opds_catalog.dl.subprocess.Popen", side_effect=_fake_converter):
                response = client.get(
                    reverse("opds:convert", args=(create_regular_book.id, "epub"))
                )
        assert response.status_code == 200
        assert response["Content-Length"] != "0"

    def test_convert_compressed_book_decompresses_before_converting(
        self, client, catalog, override_config, tmp_path, test_rootlib
    ) -> None:
        """CAT_ZIP: конвертер должен получить РАСПАКОВАННОЕ содержимое, не сам .zip.

        Реальный сценарий: книга сжата функцией "Сжать" библиотеки
        (fb2parser_core.compress_service) в .fb2.zip. ConvertFB2 обязан
        сначала распаковать её во временный .fb2 (см. ветку cat_type in
        [CAT_ZIP, CAT_INP] в dl.py) — конвертер никогда не должен увидеть
        сжатые байты напрямую.
        """
        book = Book.objects.create(
            title="Zipped book", search_title="ZIPPED BOOK",
            format="fb2", filename="262001.fb2",
            path="262001.zip", cat_type=opdsdb.CAT_ZIP, catalog=catalog,
        )
        # Сравниваем с содержимым ИЗ САМОГО .zip, а не с отдельным .fb2 на
        # диске — git на Windows-чекауте может перекодировать переводы строк
        # в текстовых файлах (LF→CRLF), тогда как бинарное содержимое .zip
        # не трогается ничем: это и есть настоящий эталон "как должно быть
        # распаковано".
        import zipfile
        with zipfile.ZipFile(os.path.join(test_rootlib, "262001.zip")) as zf:
            original_bytes = zf.read("262001.fb2")

        captured = {}

        def _capturing_converter(*args, **kwargs):
            # Читаем СЕЙЧАС — dl.py удаляет временный .fb2 сразу после
            # возврата ответа, к моменту проверки в тесте файла уже не будет.
            src = args[0][1]
            captured["path"] = src
            with open(src, "rb") as fsrc:
                captured["content"] = fsrc.read()
            return _fake_converter(*args, **kwargs)

        with override_config(
            SOPDS_FB2TOEPUB="fake-converter", SOPDS_TEMP_DIR=str(tmp_path)
        ):
            with patch(
                "opds_catalog.dl.subprocess.Popen", side_effect=_capturing_converter
            ):
                response = client.get(
                    reverse("opds:convert", args=(book.id, "epub"))
                )

        assert response.status_code == 200
        # Файл, переданный конвертеру, — это распакованное содержимое,
        # а не .zip и не путь внутрь .zip.
        assert captured["path"].endswith("262001.fb2")
        assert captured["content"] == original_bytes
