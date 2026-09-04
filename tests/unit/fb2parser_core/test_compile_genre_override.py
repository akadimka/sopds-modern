"""Регрессия для `genre_override` в `FB2CompilerService.compile_group()` и
`auto_compile_library()` — обнаружено на реальной библиотеке (34 книги
получили `<genre>other</genre>` при синхронизации, docs/quality-roadmap.md).

Жанр итогового скомпилированного файла раньше брался ТОЛЬКО из `<genre>`
метаданных первой исходной книги — у файлов-кандидатов на компиляцию
("Дилогия", "в N книгах" и т.п.) этот тег часто пуст, и компилятор жёстко
подставлял заглушку "other", хотя файлы физически уже лежат в правильной
жанровой папке библиотеки.
"""
import re
from pathlib import Path

from fb2parser_core.auto_compile_service import auto_compile_library
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_NO_GENRE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title>
<sequence name="Серия Тест" number="{num}"/>
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_: Path, filename: str, title: str, num: str) -> Path:
    p = dir_ / filename
    p.write_text(_FB2_NO_GENRE.format(title=title, num=num), encoding="utf-8")
    return p


class TestCompileGroupGenreOverride:
    def test_genre_override_used_instead_of_other(self, tmp_path):
        author_dir = tmp_path / "Автор Тест"
        author_dir.mkdir()
        _write_book(author_dir, "Автор Тест - Серия Тест 1.fb2", "Серия Тест 1", "1")
        _write_book(author_dir, "Автор Тест - Серия Тест 2.fb2", "Серия Тест 2", "2")

        from fb2parser_core import regen_csv
        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Автор Тест"]
        assert len(matches) == 1
        group = matches[0]

        result = svc.compile_group(group, output_dir=tmp_path / "out",
                                    delete_sources=False, genre_override="Фантастика")
        assert result.success
        text = result.output_path.read_text(encoding="utf-8")
        assert "<genre>Фантастика</genre>" in text
        assert "<genre>other</genre>" not in text

    def test_without_override_falls_back_to_other(self, tmp_path):
        author_dir = tmp_path / "Автор Тест"
        author_dir.mkdir()
        _write_book(author_dir, "Автор Тест - Серия Тест 1.fb2", "Серия Тест 1", "1")
        _write_book(author_dir, "Автор Тест - Серия Тест 2.fb2", "Серия Тест 2", "2")

        from fb2parser_core import regen_csv
        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        group = next(g for g in groups if g.author == "Автор Тест")

        result = svc.compile_group(group, output_dir=tmp_path / "out2", delete_sources=False)
        assert result.success
        text = result.output_path.read_text(encoding="utf-8")
        assert "<genre>other</genre>" in text

    def test_genre_folder_detected_without_explicit_override(self, tmp_path):
        """Реальный случай (docs/quality-roadmap.md, баг №19): ручной
        инструмент компиляции (normalize/compiler → compiler_run() в
        views.py) зовёт compile_group() БЕЗ genre_override вообще — 3
        файла ("Квантовые джунгли", "Игра не для всех", "Зург") получили
        genre="other", хотя физически лежали внутри "Фантастика". Теперь
        compile_group() сам находит genre-папку по пути первой книги,
        даже когда вызывающий код не передал genre_override явно.
        """
        genre_dir = tmp_path / "Фантастика" / "Автор Тест"
        genre_dir.mkdir(parents=True)
        _write_book(genre_dir, "Автор Тест - Серия Тест 1.fb2", "Серия Тест 1", "1")
        _write_book(genre_dir, "Автор Тест - Серия Тест 2.fb2", "Серия Тест 2", "2")

        from fb2parser_core import regen_csv
        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        group = next(g for g in groups if g.author == "Автор Тест")

        # Как compiler_run() — output_dir=None, без genre_override.
        result = svc.compile_group(group, output_dir=None, delete_sources=False)
        assert result.success
        text = result.output_path.read_text(encoding="utf-8")
        assert "<genre>Фантастика</genre>" in text
        assert "<genre>other</genre>" not in text


class TestAutoCompileLibraryDerivesGenreFromFolder:
    """auto_compile_library() вычисляет genre_override из первого сегмента
    пути группы относительно library_path — единственный реальный вызывающий
    код (fb2parser_web.views._run_compile_pass) всегда передаёт сюда путь до
    настоящей, организованной по жанрам библиотеки.
    """

    def test_genre_taken_from_top_level_library_folder(self, tmp_path):
        library = tmp_path / "Library"
        author_dir = library / "Фантастика" / "Автор Тест"
        author_dir.mkdir(parents=True)
        _write_book(author_dir, "Автор Тест - Серия Тест 1.fb2", "Серия Тест 1", "1")
        _write_book(author_dir, "Автор Тест - Серия Тест 2.fb2", "Серия Тест 2", "2")

        auto_compile_library(str(library), config_path=_config_path())

        compiled = list(author_dir.glob("*.fb2"))
        assert len(compiled) == 1
        text = compiled[0].read_text(encoding="utf-8")
        assert "<genre>Фантастика</genre>" in text
        assert "<genre>other</genre>" not in text
