"""Номер тома из имени файла — docs/quality-roadmap.md, баг №172.

- Постернак «Великие земли АСКАР»: «великие» — маркер сборника (для
  «Великие писатели»), а он стоит в имени самой серии — каждая книга
  считалась сборником, номер «2.» из имени файла пропускался, обе первые
  книги получали 1 из метаданных («ч. 1 в 2 книгах», третья выпадала).
- Шер «NPC»: «02, NPC. Осознание.fb2» — запятая вместо точки, номера не было.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title>
{sequence}
</title-info>
</description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_COLLECTION = ("СЕРИЯ.LitRPG", "Hеофициальная серия книг (разные издательства)")


def _write(root, folder, series, filename, title, first, last, sn=None):
    d = root.joinpath(*_COLLECTION, folder, series)
    d.mkdir(parents=True, exist_ok=True)
    seq = f'<sequence name="{series}" number="{sn}"/>' if sn else ""
    (d / filename).write_text(_FB2.format(first=first, last=last, title=title, sequence=seq), encoding="utf-8")


def _numbers(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").rsplit("/", 1)[1]: r.series_number for r in records}


def test_collection_marker_inside_series_name_does_not_block_prefix(tmp_path):
    s = "Великие земли АСКАР"
    for fn, sn in (("1. Братство Крови.fb2", "1"), ("2. Кредо наемника.fb2", "1"), ("3. Путь изгоя.fb2", "3")):
        _write(tmp_path, "Постернак-Александр", s, fn, f"{s}: {fn[3:-4]}", "Александр", "Постернак", sn)
    assert _numbers(tmp_path) == {"1. Братство Крови.fb2": "1", "2. Кредо наемника.fb2": "2",
                                  "3. Путь изгоя.fb2": "3"}


def test_comma_after_number_prefix(tmp_path):
    for fn in ("01. NPC. Пробуждение.fb2", "02, NPC. Осознание.fb2", "03. NPC 3. Заточение.fb2"):
        _write(tmp_path, "Шер-Вениамин", "NPC", fn, fn[4:-4], "Вениамин", "Шер")
    assert _numbers(tmp_path) == {"01. NPC. Пробуждение.fb2": "1", "02, NPC. Осознание.fb2": "2",
                                  "03. NPC 3. Заточение.fb2": "3"}
