"""Регрессия для `FB2CompilerService.compile_group()` — обнаружено при
проверке смешанных данных (docs/quality-roadmap.md, баг №20, часть 2):
библиотека частично сжата функцией "Сжать" (Library → Compress,
`.fb2.zip`), и при дополнении такой серии новым томом `compile_group()`
всегда писал результат ПЛОСКИМ `.fb2` — молча "расжимая" уже сжатую
серию обратно, рассинхронизируя её формат с остальной сжатой
библиотекой (пользователю пришлось бы вручную пересжимать её заново).

Фикс: если среди исходников группы есть уже сжатый `.fb2.zip`
(например, ранее скомпилированный и сжатый файл серии, теперь
дополняемый новым томом), результат тоже пишется сжатым — через
`fb2_utils.write_fb2_bytes()`.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.fb2_utils import compress_fb2_file, read_fb2_bytes
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
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
    p.write_text(_FB2.format(title=title, num=num), encoding="utf-8")
    return p


class TestCompileGroupPreservesCompression:
    def test_output_compressed_when_a_source_is_compressed(self, tmp_path):
        author_dir = tmp_path / "Автор Тест"
        author_dir.mkdir()
        _write_book(author_dir, "Автор Тест - Серия Тест 1.fb2", "Серия Тест 1", "1")
        book2 = _write_book(author_dir, "Автор Тест - Серия Тест 2.fb2", "Серия Тест 2", "2")
        compress_fb2_file(book2)  # -> "...2.fb2.zip", исходник удалён

        from fb2parser_core import regen_csv
        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        group = next(g for g in groups if g.author == "Автор Тест")

        result = svc.compile_group(group, output_dir=tmp_path / "out",
                                    delete_sources=False, genre_override="Фантастика")
        assert result.success
        assert result.output_path.name.lower().endswith(".fb2.zip")
        content = read_fb2_bytes(result.output_path).decode("utf-8")
        assert "<genre>Фантастика</genre>" in content
        assert content.count("Текст.") == 2  # оба тома реально попали в тело

    def test_output_stays_plain_when_no_source_is_compressed(self, tmp_path):
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

        result = svc.compile_group(group, output_dir=tmp_path / "out2",
                                    delete_sources=False, genre_override="Фантастика")
        assert result.success
        assert result.output_path.name.lower().endswith(".fb2")
        assert not result.output_path.name.lower().endswith(".fb2.zip")
