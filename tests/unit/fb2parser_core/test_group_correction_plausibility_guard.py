"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-roadmap.md,
баг №68.

Реальный случай (Клеванский Кирилл / "Сердце Дракона", продолжение
анализа багов №66/№67): групповая коррекция "большинство книг доверяет
метаданным → откатить filename-книги на мета-номер" не проверяла
правдоподобность самого числа. 1 файл серии из ~20 несёт битую метадату
стороннего инструмента (`<sequence number="99">`, ничем не
подтверждённую), при этом верный номер уже корректно извлечён из
имени/заголовка файла ("Том 5"). Большинство остальных файлов честно
используют метаданные — это давало основание сработать коррекции и
затереть верный filename-номер битым "99", отрывая том 5 от остальной
серии (взлетает на позицию 99, вне какого-либо диапазона — и полностью
пропадает из компиляции, не попадая ни в группу, ни в дубликаты).
"""
from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title>
<sequence name="{series}" number="{num}"/>
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_, filename, title, series, num):
    p = dir_ / filename
    p.write_text(_FB2.format(title=title, series=series, num=num), encoding="utf-8")
    return p


class TestGroupCorrectionRejectsImplausibleMetadataJump:
    def test_wildly_off_metadata_number_not_applied(self, tmp_path):
        author_dir = tmp_path / "Автор Тест"
        author_dir.mkdir()
        for n in (1, 2, 3, 4):
            _write_book(author_dir, f"Автор Тест - Огнебор {n}.fb2",
                        f"Огнебор {n}", "Огнебор", str(n))
        _write_book(author_dir, "Автор Тест - Огнебор. Том 5.fb2",
                    "Огнебор. Том 5", "Огнебор", "99")

        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Автор Тест"]
        assert len(matches) == 1, f"expected 1 merged group (1-5), got {len(matches)}"
        book5 = next(b for b in matches[0].books if "Том 5" in b.abs_path.name)
        assert book5.sort_key[1] == 5
