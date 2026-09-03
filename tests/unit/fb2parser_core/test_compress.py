"""Тесты сжатия .fb2 -> .fb2.zip (fb2_utils.compress_fb2_file /
compress_service.compress_library) — пункт меню Library → Compress.
"""
import zipfile

import pytest

from fb2parser_core.compress_service import compress_library
from fb2parser_core.fb2_utils import compress_fb2_file


def _make_fb2(dir_, name, content=b"<FictionBook>hello</FictionBook>" * 100):
    path = dir_ / name
    path.write_bytes(content)
    return path


class TestCompressFb2File:
    def test_replaces_plain_fb2_with_zip(self, tmp_path):
        original = _make_fb2(tmp_path, "Book.fb2")
        original_content = original.read_bytes()

        saved = compress_fb2_file(original)

        assert not original.exists()
        zipped = tmp_path / "Book.fb2.zip"
        assert zipped.exists()
        with zipfile.ZipFile(zipped) as zf:
            assert zf.namelist() == ["Book.fb2"]
            assert zf.read("Book.fb2") == original_content
        assert saved > 0

    def test_rejects_already_zipped_file(self, tmp_path):
        zipped = tmp_path / "Book.fb2.zip"
        with zipfile.ZipFile(zipped, "w") as zf:
            zf.writestr("Book.fb2", b"<FictionBook/>")
        with pytest.raises(ValueError):
            compress_fb2_file(zipped)
        assert zipped.exists()  # не тронут


class TestCompressLibrary:
    def test_compresses_only_plain_fb2_files(self, tmp_path):
        _make_fb2(tmp_path, "A.fb2")
        _make_fb2(tmp_path, "B.fb2")
        already = tmp_path / "C.fb2.zip"
        with zipfile.ZipFile(already, "w") as zf:
            zf.writestr("C.fb2", b"<FictionBook/>")

        result = compress_library(tmp_path)

        assert result["compressed"] == 2
        assert result["errors"] == []
        assert result["bytes_saved"] > 0
        assert not (tmp_path / "A.fb2").exists()
        assert not (tmp_path / "B.fb2").exists()
        assert (tmp_path / "A.fb2.zip").exists()
        assert (tmp_path / "B.fb2.zip").exists()
        # Уже сжатый файл не тронут и не пересчитан.
        with zipfile.ZipFile(already) as zf:
            assert zf.namelist() == ["C.fb2"]

    def test_reports_progress(self, tmp_path):
        _make_fb2(tmp_path, "A.fb2")
        _make_fb2(tmp_path, "B.fb2")
        seen = []
        compress_library(tmp_path, progress_callback=lambda done, total, cur: seen.append((done, total, cur)))
        assert len(seen) == 2
        assert seen[0][1] == 2  # total

    def test_stop_check_halts_early(self, tmp_path):
        _make_fb2(tmp_path, "A.fb2")
        _make_fb2(tmp_path, "B.fb2")
        result = compress_library(tmp_path, stop_check=lambda: True)
        assert result["stopped"] is True
        assert result["compressed"] == 0
