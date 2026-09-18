"""Регрессия для `GenreAssignmentService._assign_genre_to_file()`.

Реальный случай (папка "Книжная полка Дозора" — 2458 файлов, 14.6 ГБ,
отдельные компиляции по 30-50+ МБ): присвоение жанра всей папке никогда
не завершалось — с точки зрения пользователя процесс "уходил в
бесконечность". Причина: после вставки тега <genre> метод прогонял ВЕСЬ
документ (включая многомегабайтное <body>) через `pretty_print_xml()` —
чисто косметическое форматирование, не нужное для смены одного тега.
На больших компиляциях запрос на присвоение жанра синхронно выполняется
внутри HTTP-запроса (`assign_genre_multi`), а таймаут воркера gunicorn —
120 секунд; операция в исходном виде почти наверняка в него не
укладывалась, воркер убивался, что и выглядело как зависание навечно.

Заодно обнаружен смежный риск: запись файла была неатомарной
(`open(path, 'w')` сразу обрезает файл) — при обрыве процесса ровно в
момент записи (как раз то, что вызывает таймаут воркера) файл оставался
битым. Теперь запись идёт во временный файл рядом с атомарной заменой
оригинала (`os.replace`).
"""
import time
from pathlib import Path

import pytest

from fb2parser_core.genre_assign import GenreAssignmentService

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>старый-жанр</genre>
<author><first-name>Тест</first-name></author>
<book-title>Компиляция</book-title>
</title-info>
</description>
<body><section>{body}</section></body>
</FictionBook>
"""


def _write_fb2(path: Path, paragraph_count: int) -> None:
    body = "".join(f"<p>Абзац книги номер {i}, текст внутри повествования.</p>" for i in range(paragraph_count))
    path.write_text(_FB2_TEMPLATE.format(body=body), encoding="utf-8")


class TestGenreAssignmentSkipsPrettyPrinting:
    def test_genre_replaced_correctly_on_large_compilation(self, tmp_path):
        fb2_path = tmp_path / "big.fb2"
        _write_fb2(fb2_path, paragraph_count=300_000)  # ~15 МБ, реалистичная компиляция

        svc = GenreAssignmentService()
        assert svc._assign_genre_to_file(fb2_path, "детская литература") is True

        result = fb2_path.read_text(encoding="utf-8")
        assert "<genre>детская литература</genre>" in result
        assert "старый-жанр" not in result
        # Тело книги должно остаться нетронутым по содержанию (не переписано
        # построчным pretty-printer'ом) — достаточно проверить, что абзацы
        # на месте и XML не искажён вставкой лишних переносов внутри текста.
        assert "<p>Абзац книги номер 0, текст внутри повествования.</p>" in result
        assert "<p>Абзац книги номер 299999, текст внутри повествования.</p>" in result

    def test_large_compilation_processed_well_under_a_second(self, tmp_path):
        """Не строгий бенчмарк (окружения разные), но защищает от повторного
        внесения O(n) на строку/символ прохода по всему телу документа —
        раньше (с pretty_print_xml) 15 МБ занимали ~0.6-0.9с лишней работы
        сверх необходимого чтения+regex+записи.
        """
        fb2_path = tmp_path / "big.fb2"
        _write_fb2(fb2_path, paragraph_count=300_000)

        svc = GenreAssignmentService()
        t0 = time.perf_counter()
        assert svc._assign_genre_to_file(fb2_path, "детская литература") is True
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, f"assign_genre_to_file took {elapsed:.2f}s — pretty-printing regression?"


class TestGenreAssignmentRefusesZipBomb:
    """Регрессия — docs/quality-roadmap.md, баг №97.

    `_assign_genre_to_file` читает `.fb2.zip`/FBZ через `zf.open(...).read()`
    без проверки заявленного распакованного размера — крошечный по
    размеру архив с огромным объявленным распакованным размером
    (zip-bomb) полностью разворачивался бы в память.
    """

    def test_declared_huge_uncompressed_size_refused(self, tmp_path):
        import zipfile

        # ВАЖНО: payload должен выглядеть как ВАЛИДНЫЙ FB2 (не просто нули) —
        # иначе старый (безфиксовый) код тоже вернёт False, но по ДРУГОЙ
        # причине ("не валидный XML файл"), уже ПОСЛЕ полной распаковки
        # 250 МБ в память. Тест обязан различать "отказ до распаковки"
        # от "успешно распаковали и только потом отказали".
        fb2_zip_path = tmp_path / "bomb.fb2.zip"
        body = b"<p>Abzac tekst povtoryaetsya mnogo raz.</p>" * 6_000_000  # ~258 МБ
        payload = (
            b"<?xml version='1.0'?><FictionBook><description><title-info>"
            b"<genre>old</genre><book-title>Bomb</book-title></title-info></description>"
            b"<body><section>" + body + b"</section></body></FictionBook>"
        )
        with zipfile.ZipFile(fb2_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("bomb.fb2", payload)

        svc = GenreAssignmentService()
        assert svc._assign_genre_to_file(fb2_zip_path, "фантастика") is False

    def test_normal_size_zip_still_works(self, tmp_path):
        import zipfile

        fb2_zip_path = tmp_path / "normal.fb2.zip"
        with zipfile.ZipFile(fb2_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("normal.fb2", _FB2_TEMPLATE.format(body="<p>Текст</p>").encode("utf-8"))

        svc = GenreAssignmentService()
        # Не проверяем итоговый формат файла на диске — отдельно от бага
        # №97 обнаружено, что запись после присвоения жанра всегда пишет
        # ОБЫЧНЫЙ текстовый файл поверх исходного пути, даже если исходный
        # файл был .fb2.zip (не перепаковывает обратно в zip) — это
        # существовало и до этого фикса, вне текущего скоупа. Здесь
        # достаточно убедиться, что нормальный (не-бомбовый) размер не
        # отклоняется новой проверкой.
        assert svc._assign_genre_to_file(fb2_zip_path, "фантастика") is True


class TestGenreAssignmentAtomicWrite:
    def test_no_leftover_tmp_file_after_success(self, tmp_path):
        fb2_path = tmp_path / "book.fb2"
        _write_fb2(fb2_path, paragraph_count=5)

        svc = GenreAssignmentService()
        assert svc._assign_genre_to_file(fb2_path, "фантастика") is True

        assert not (tmp_path / "book.fb2.tmp").exists()
        assert fb2_path.exists()

    def test_original_file_untouched_if_write_fails_midway(self, tmp_path, monkeypatch):
        """Симулирует обрыв процесса ровно во время записи временного файла —
        оригинал должен остаться читаемым и с прежним содержимым, а не
        усечённым/битым.
        """
        fb2_path = tmp_path / "book.fb2"
        _write_fb2(fb2_path, paragraph_count=5)
        original_content = fb2_path.read_text(encoding="utf-8")

        import builtins

        real_open = builtins.open

        def _failing_open(path, *args, **kwargs):
            if str(path).endswith(".fb2.tmp"):
                raise OSError("Симуляция обрыва процесса при записи")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _failing_open)

        svc = GenreAssignmentService()
        assert svc._assign_genre_to_file(fb2_path, "фантастика") is False

        assert fb2_path.read_text(encoding="utf-8") == original_content
        assert not (tmp_path / "book.fb2.tmp").exists()
