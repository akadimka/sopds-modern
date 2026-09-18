"""Регрессия — docs/quality-roadmap.md, баг №95.

Найдено при архитектурном аудите: `FOLDER`/`FILE`/`EXT` в .inp-записи
внутри .inpx-каталога приходят из стороннего архива (типичный
источник — сторонние файлообменники) без проверки и напрямую
участвуют в построении пути к файлу на диске
(`zip_file = os.path.join(self.inpx_catalog, meta_data[sFolder])`),
а затем — в `book.path`/`book.filename` в БД, которые в конце концов
читает `opds_catalog.utils.getFileData()`.
"""
import os
import zipfile
from io import BytesIO

import pytest

from opds_catalog.inpx_parser import (
    Inpx,
    _is_safe_relative_path,
    sAuthor,
    sDate,
    sDel,
    sExt,
    sFile,
    sFolder,
    sGenre,
    sLang,
    sLibId,
    sSerNo,
    sSeries,
    sSize,
    sTitle,
)


class TestIsSafeRelativePath:
    def test_plain_relative_path_is_safe(self):
        assert _is_safe_relative_path("some/sub/folder") is True

    def test_empty_is_safe(self):
        assert _is_safe_relative_path("") is True

    def test_dotdot_is_unsafe(self):
        assert _is_safe_relative_path("../secret") is False
        assert _is_safe_relative_path("a/../../secret") is False

    def test_absolute_path_is_unsafe(self):
        assert _is_safe_relative_path(os.path.join("C:" + os.sep, "Windows")) is False
        assert _is_safe_relative_path(os.sep + "etc") is False


def _build_malicious_inpx(tmp_path, folder_value="../../secret"):
    """Собирает .inpx-архив с одной .inp-записью, у которой FOLDER
    указывает за пределы каталога библиотеки."""
    fields = [
        sAuthor, sGenre, sTitle, sSeries, sSerNo, sFile,
        sSize, sLibId, sDel, sExt, sDate, sLang, sFolder,
    ]
    values = {
        sAuthor: "Test", sGenre: "", sTitle: "Leak", sSeries: "",
        sSerNo: "", sFile: "leak", sSize: "10", sLibId: "1",
        sDel: "0", sExt: "fb2", sDate: "", sLang: "ru",
        sFolder: folder_value,
    }
    line = "\x04".join(values[f] for f in fields) + "\n"

    inpx_path = tmp_path / "malicious.inpx"
    with zipfile.ZipFile(inpx_path, "w") as zf:
        zf.writestr("structure.info", ";".join(fields))
        zf.writestr("books.inp", line.encode("utf-8"))
    return str(inpx_path)


class TestInpxParserSkipsUnsafeFolder:
    def test_malicious_folder_record_is_skipped(self, tmp_path):
        inpx_path = _build_malicious_inpx(tmp_path, folder_value="../../secret")
        captured = []
        parser = Inpx(inpx_path, lambda inpx, inp, meta: captured.append(meta))
        parser.parse()

        assert captured == []

    def test_normal_folder_record_is_kept(self, tmp_path):
        inpx_path = _build_malicious_inpx(tmp_path, folder_value="normal/subfolder")
        captured = []
        parser = Inpx(inpx_path, lambda inpx, inp, meta: captured.append(meta))
        parser.parse()

        assert len(captured) == 1
        assert captured[0][sFolder].rstrip("\n") == "normal/subfolder"

    def test_file_and_ext_stripped_to_basename(self, tmp_path):
        inpx_path = _build_malicious_inpx(tmp_path, folder_value="normal")
        # Подменяем FILE на значение с обходом каталога уже после сборки —
        # проще пересобрать с другими values напрямую.
        fields = [
            sAuthor, sGenre, sTitle, sSeries, sSerNo, sFile,
            sSize, sLibId, sDel, sExt, sDate, sLang, sFolder,
        ]
        values = {
            sAuthor: "Test", sGenre: "", sTitle: "Leak", sSeries: "",
            sSerNo: "", sFile: "../../../etc/leak", sSize: "10", sLibId: "1",
            sDel: "0", sExt: "fb2", sDate: "", sLang: "ru",
            sFolder: "normal",
        }
        line = "\x04".join(values[f] for f in fields) + "\n"
        inpx_path2 = tmp_path / "malicious2.inpx"
        with zipfile.ZipFile(inpx_path2, "w") as zf:
            zf.writestr("structure.info", ";".join(fields))
            zf.writestr("books.inp", line.encode("utf-8"))

        captured = []
        parser = Inpx(str(inpx_path2), lambda inpx, inp, meta: captured.append(meta))
        parser.parse()

        assert len(captured) == 1
        assert captured[0][sFile] == "leak"
        assert "/" not in captured[0][sFile] and ".." not in captured[0][sFile]
