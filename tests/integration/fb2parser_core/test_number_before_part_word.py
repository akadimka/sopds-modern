"""«N. M часть …» — части одной книги, docs/quality-roadmap.md, баг №174.

Стариков «Игра в Жизнь»: «2. 1 часть Великого похода. …» … «2. 4 часть …»
— четыре части книги 2. Распознавался только «Том N. Часть M»; части
вставали на одну позицию 2 в случайном порядке.
"""
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Антон</first-name><last-name>Стариков</last-name></author>
<book-title>{title}</book-title>
<sequence name="Игра в Жизнь" number="{sn}"/>
</title-info>
</description>
<body><section><p>{text}</p></section></body>
</FictionBook>
"""


def test_parts_numbered_before_word_are_ordered(tmp_path):
    d = tmp_path / "СЕРИЯ.LitRPG" / "Hеофициальная серия книг (разные издательства)" / "Стариков-Антон" / "Игра в Жизнь"
    d.mkdir(parents=True)
    files = ["1. Игра в Жизнь.fb2", "2. 4 часть Великого похода. Исход.fb2",
             "2. 3 часть Великого похода. Сброшенные хвосты.fb2", "2. 1 часть Великого похода. Великий поход.fb2",
             "2. 2 часть Великого похода. От океана до степи.fb2", "3. 1 часть Пришельцы.fb2"]
    for i, fn in enumerate(files):
        text = " ".join(f"книга{i}слово{j}" for j in range(400))
        (d / fn).write_text(_FB2.format(title=fn[3:-4], sn=fn[0], text=text), encoding="utf-8")
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    groups = [g for g in FB2CompilerService().find_groups(records, tmp_path) if g.books]
    assert len(groups) == 1
    assert [b.abs_path.name for b in groups[0].books] == [
        "1. Игра в Жизнь.fb2",
        "2. 1 часть Великого похода. Великий поход.fb2",
        "2. 2 часть Великого похода. От океана до степи.fb2",
        "2. 3 часть Великого похода. Сброшенные хвосты.fb2",
        "2. 4 часть Великого похода. Исход.fb2",
        "3. 1 часть Пришельцы.fb2",
    ]


def test_prefix_num_part_pattern():
    ext = FB2CompilerService._extract_volume_part
    assert ext("", "2. 1 часть Великого похода. Исход") == (2, 1)
    assert ext("", "2. 3-я часть Похода") == (2, 3)
    assert ext("", "12. Том 1. Часть 2") == (1, 2)          # прежний шаблон
    assert ext("", "2. Часть света") is None
