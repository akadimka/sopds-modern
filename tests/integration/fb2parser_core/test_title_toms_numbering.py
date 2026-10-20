"""Нумерация по томам из названий — docs/quality-roadmap.md, баг №168.

Реальный случай (Amazerak / «Демоническая экспансия»): «01. … Том 1 и
Том 2.fb2» + «02. … Том 3.fb2» с <sequence number="02">. Префикс и
метаданные считают изданные книги, название — тома. Правило внутреннего
тома (№62) давало второй книге «2.3», фильтр ложных сборников (№142)
сбрасывал «1-2» первой в «1» — компиляция «(Дилогия)», диапазон «1-2.3».
В «СЕРИЯ.LitRPG» так 10 групп (Вяч, Губарев, Дорничев, Манцуров…).
Решение пользователя: нумерация и название — по томам («Трилогия»).
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>{title}</book-title>
{sequence}
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>{text}</p></section>
</body>
</FictionBook>
"""


def _write(dir_: Path, filename, title, series, sn=None):
    seq = f'<sequence name="{series}" number="{sn}"/>' if sn else ""
    # у каждой книги свой текст — иначе страховка №141 сочтёт их одной книгой
    text = " ".join(f"{filename[:3]}слово{j}" for j in range(400))
    (dir_ / filename).write_text(_FB2.format(title=title, sequence=seq, text=text), encoding="utf-8")


def _group(tmp_path, series, books):
    series_dir = tmp_path / "Волков Иван" / series
    series_dir.mkdir(parents=True)
    for filename, title, sn in books:
        _write(series_dir, filename, title, series, sn)
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    compiler = FB2CompilerService()
    groups = [g for g in compiler.find_groups(records, tmp_path) if g.books]
    assert len(groups) == 1, [(g.series, g.volume_range) for g in groups]
    group = groups[0]
    labels = [(b.volume_label, b.abs_path.name) for b in group.books]
    return group, labels, compiler.compute_group_suffix(group)[0]


def test_first_file_with_two_toms_then_single_toms(tmp_path):
    group, labels, suffix = _group(tmp_path, "Демоническая экспансия", [
        ("01. Демоническая экспансия. Том 1 и Том 2.fb2", "Демоническая экспансия. Том 1 и Том 2", None),
        ("02. Демоническая экспансия. Том 3.fb2", "Демоническая экспансия. Том 3", "02"),
    ])
    assert labels == [("1-2", "01. Демоническая экспансия. Том 1 и Том 2.fb2"),
                      ("3", "02. Демоническая экспансия. Том 3.fb2")]
    assert group.volume_range == "1-3"
    assert suffix == "Трилогия"
    assert not group.duplicate_paths


def test_several_files_with_tom_ranges(tmp_path):
    """Моргот-Эл «Злодейский путь!..»: [том 1-2], [том 3-4], [том 5]."""
    group, labels, suffix = _group(tmp_path, "Злодейский путь", [
        ("1. Злодейский путь [том 1-2].fb2", "Злодейский путь [том 1-2]", "1"),
        ("2. Злодейский путь [том 3-4].fb2", "Злодейский путь [том 3-4]", "2"),
        ("3. Злодейский путь [том 5].fb2", "Злодейский путь [том 5]", "3"),
    ])
    assert [lbl for lbl, _ in labels] == ["1-2", "3-4", "5"]
    assert group.volume_range == "1-5"
    assert suffix == "Пенталогия"
    assert not group.duplicate_paths


def test_long_series_numbered_by_toms(tmp_path):
    """Вяч «Сила рода», Розальев, Дорничев: префикс N, sn N, «Том N+1»."""
    books = [("01. Сила рода. Том 1 и Том 2.fb2", "Сила рода. Том 1 и Том 2", "1")]
    books += [(f"0{i}. Сила рода. Том {i + 1}.fb2", f"Сила рода. Том {i + 1}", str(i)) for i in range(2, 6)]
    group, labels, _ = _group(tmp_path, "Сила рода", books)
    assert [lbl for lbl, _ in labels] == ["1-2", "3", "4", "5", "6"]
    assert group.volume_range == "1-6"


def test_inner_tom_without_toms_on_every_book_unchanged(tmp_path):
    """«Сфера Богов»: «Том» только у 12-й книги — правило №62 как раньше."""
    group, labels, _ = _group(tmp_path, "Сфера Богов", [
        ("11. Сфера Богов.fb2", "Сфера Богов", "11"),
        ("12. Сфера Богов том 1.fb2", "Сфера Богов том 1", "12"),
    ])
    assert labels == [("11", "11. Сфера Богов.fb2"), ("12.1", "12. Сфера Богов том 1.fb2")]


def test_toms_with_a_gap_are_not_renumbered(tmp_path):
    """Тома 1-2 и 4 — ряд не сходится, по томам не нумеруем."""
    _, labels, _ = _group(tmp_path, "Восходитель", [
        ("01. Восходитель. Том 1 и 2.fb2", "Восходитель. Том 1 и 2", None),
        ("02. Восходитель. Том 4.fb2", "Восходитель. Том 4", "02"),
    ])
    assert "4" not in [lbl for lbl, _ in labels]


def test_title_tom_span():
    def span(title):
        book = type("B", (), {})()
        book.record = type("R", (), {"file_title": title})()
        book.abs_path = Path(title + ".fb2")
        return FB2CompilerService._title_tom_span(book)

    assert span("Демоническая экспансия. Том 1 и Том 2") == (1, 2)
    assert span("Рагнарёк Онлайн. Трансмигратор ( Том 1 и 2 )") == (1, 2)
    assert span("Пожиратель душ. Том 1, Том 2") == (1, 2)
    assert span("Чистилище. Том 1-2") == (1, 2)
    assert span("Право Элиты. Книга вторая. том 6") == (6, 6)
    assert span("Злодейский путь!.. [том 9]") == (9, 9)
    assert span("Атом 5") is None                 # «том» внутри слова
    assert span("Том 1. Часть 2. Том 3") is None  # два отдельных «Тома»
    assert span("Сфера Богов") is None
