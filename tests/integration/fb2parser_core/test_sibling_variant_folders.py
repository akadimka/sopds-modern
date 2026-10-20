"""Папка-вариант рядом с папкой серии — docs/quality-roadmap.md, баг №173.

Решение пользователя (№157): вариант («Перезагрузка», «Новая история»…) —
отдельная серия. №157 разделял варианты-ПОДПАПКИ («Свободные агенты/Версия
2.0»); соседние папки сливались: извлечение серии срезало «. Новая
история», нормализация — «(Перезагрузка)» как пометку автора, Pass 4 делал
из «Старатели. Новая история» подсерию — номера книг сталкивались (Лей
«Старатели», Roy «Избранный Богами», Imeninik «… (Ugly Bastard)»).
"""
import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
{authors}
<book-title>{title}</book-title>
</title-info>
</description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_COLLECTION = ("СЕРИЯ.LitRPG", "Hеофициальная серия книг (разные издательства)")


def _series(tmp_path, author_folder, layout, authors="<author><nickname>Автор</nickname></author>"):
    for folder, files in layout.items():
        d = tmp_path.joinpath(*_COLLECTION, author_folder, folder)
        d.mkdir(parents=True)
        for fn in files:
            (d / fn).write_text(_FB2.format(authors=authors, title=fn[:-4]), encoding="utf-8")
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").split("/")[3]: r.proposed_series for r in records}


@pytest.mark.parametrize("main, variant", [
    ("Старатели", "Старатели. Новая история"),
    ("Избранный богами", "Избранный Богами (Перезагрузка)"),
    ("Вот такая вот, фентези жизнь", "Вот такая вот, фентези жизнь (Ugly Bastard)"),
])
def test_sibling_variant_folder_is_separate_series(tmp_path, main, variant):
    got = _series(tmp_path, "Лей-Влад", {
        main: ["01. Первая.fb2", "02. Вторая.fb2"],
        variant: ["01. Начало.fb2", "02. Продолжение.fb2"],
    })
    assert got[main].lower() == main.lower()
    assert got[variant] == variant


def test_suffix_without_sibling_still_stripped(tmp_path):
    """Нет папки «Сага» рядом — «(Иванов)» по-прежнему пометка автора."""
    got = _series(tmp_path, "Лей-Влад", {"Сага (Иванов)": ["01. Первая.fb2", "02. Вторая.fb2"]})
    assert got["Сага (Иванов)"] == "Сага"


def test_original_script_name_not_a_coauthor(tmp_path):
    """Roy: серия из одной книги, в метаданных «Roy; Ririnra; りりんら»."""
    meta = "".join(f"<author><nickname>{n}</nickname></author>" for n in ("Roy", "Ririnra", "りりんら"))
    _series(tmp_path, "Roy", {"Избранный Богами (Перезагрузка)": ["01. Том 1.fb2"],
                              "Избранный богами": ["1. Том 1.fb2", "2. Том 2.fb2"]}, authors=meta)
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    assert {r.proposed_author for r in records} == {"Roy"}
