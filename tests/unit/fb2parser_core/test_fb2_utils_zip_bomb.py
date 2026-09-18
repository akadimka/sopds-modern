"""Регрессия для `read_fb2_bytes()` — docs/quality-roadmap.md, баг №97.

Найдено при архитектурном аудите: `read_fb2_bytes()` открывает
`.fb2.zip` и вызывает `zf.read(fb2_name)` без проверки заявленного
распакованного размера — крошечный по размеру архив с огромным
объявленным распакованным размером (zip-bomb) полностью
разворачивался бы в память. Функция на горячем пути практически
любой операции над файлом: хеширование при кешировании метаданных
(`metadata_cache.py`), синхронизация, компиляция.
"""
import zipfile
from pathlib import Path

from fb2parser_core.fb2_utils import MAX_FB2_UNCOMPRESSED_SIZE, read_fb2_bytes


class TestReadFb2BytesRefusesZipBomb:
    def test_declared_huge_uncompressed_size_falls_back_to_raw_bytes(self, tmp_path):
        fb2_zip_path = tmp_path / "bomb.fb2.zip"
        # Валидно выглядящий FB2, не просто нули — иначе результат
        # (сжатые байты вместо распакованных) совпал бы с "нормальным"
        # исходом чисто случайно и тест ничего бы не доказывал.
        body = b"<p>Abzac tekst povtoryaetsya mnogo raz.</p>" * 6_000_000  # ~258 МБ
        payload = (
            b"<?xml version='1.0'?><FictionBook><body>" + body + b"</body></FictionBook>"
        )
        with zipfile.ZipFile(fb2_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.fb2", payload)

        result = read_fb2_bytes(fb2_zip_path)

        # Отказ от распаковки -> откат на исходные (сжатые) байты вместо
        # разворачивания четверти гигабайта в память.
        assert len(result) < MAX_FB2_UNCOMPRESSED_SIZE
        assert result == fb2_zip_path.read_bytes()
        assert result != payload

    def test_normal_zip_still_reads_uncompressed_content(self, tmp_path):
        fb2_zip_path = tmp_path / "normal.fb2.zip"
        content = b"<?xml version='1.0'?><FictionBook><body>x</body></FictionBook>"
        with zipfile.ZipFile(fb2_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("normal.fb2", content)

        assert read_fb2_bytes(fb2_zip_path) == content

    def test_plain_non_zip_fb2_unaffected(self, tmp_path):
        fb2_path = tmp_path / "plain.fb2"
        content = b"<?xml version='1.0'?><FictionBook><body>x</body></FictionBook>"
        fb2_path.write_bytes(content)

        assert read_fb2_bytes(fb2_path) == content
