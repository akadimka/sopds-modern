"""Папки авторов с пометками и опечатками — docs/quality-roadmap.md, баг №171.

Реальные случаи «СЕРИЯ.LitRPG»:
- «Yoonaanee Maiks (пер. Грек-Максим)» — автором становился переводчик
  («Пер Грек-Максим»): шаблон «Серия (Автор)» из настроек;
- «Сигел-Джейсон (соавт. Миллер-Кирстен)» — точка в «соавт.» делила папку
  как «Фамилия. Серия», серия «Миллер-Кирстен)\\Иномирье. Otherworld»;
- «Бакланов-Серегей» (опечатка) — папка автора уходила в корень серии;
- «В.-Виктор (Виктор В.)» — папка автора становилась серией «В.-Виктор».
"""
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


def _author(first="", last="", nick=""):
    if nick:
        return f"<author><nickname>{nick}</nickname></author>"
    return f"<author><first-name>{first}</first-name><last-name>{last}</last-name></author>"


def _write(root, *parts, authors):
    path = root.joinpath(*_COLLECTION, *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(authors=authors, title=path.stem), encoding="utf-8")


def _records(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").split("/", 2)[2]: r for r in records}


def test_translator_in_brackets_is_not_the_author(tmp_path):
    folder = "Yoonaanee Maiks (пер. Грек-Максим)"
    _write(tmp_path, folder, "Последний Выживший", "01. Том 1.fb2", authors=_author(nick="chromatograph"))
    _write(tmp_path, folder, "Последний Выживший", "02. Том 2.fb2", authors=_author(nick="chromatograph"))
    recs = _records(tmp_path)
    assert {r.proposed_author for r in recs.values()} <= {"Yoonaanee Maiks", "Maiks Yoonaanee"}
    assert {r.proposed_series for r in recs.values()} == {"Последний Выживший"}


def test_coauthor_note_does_not_split_into_series(tmp_path):
    folder = "Сигел-Джейсон (соавт. Миллер-Кирстен)"
    both = _author("Кирстен", "Миллер") + _author("Джейсон", "Сигел")
    _write(tmp_path, folder, "Иномирье. Otherworld", "1. Otherworld.fb2", authors=both)
    _write(tmp_path, folder, "Иномирье. Otherworld", "2. Otherearth.fb2", authors=both)
    recs = _records(tmp_path)
    assert {r.proposed_series for r in recs.values()} == {"Иномирье. Otherworld"}


def test_author_folder_with_typo_not_series_root(tmp_path):
    meta = _author("Серега", "Бакланов")
    _write(tmp_path, "Бакланов-Серегей", "Рыцарь замка трёх рек", "1. Катастрофа.fb2", authors=meta)
    _write(tmp_path, "Бакланов-Серегей", "Рыцарь замка трёх рек", "2. Путь.fb2", authors=meta)
    recs = _records(tmp_path)
    series = {r.proposed_series for r in recs.values()}
    assert len(series) == 1 and "Бакланов" not in series.pop()


def test_pseudonym_and_name_in_brackets_is_author_folder(tmp_path):
    _write(tmp_path, "В.-Виктор (Виктор В.)", "РПГ 2.0.fb2", authors=_author("Виктор", "В."))
    rec = next(iter(_records(tmp_path).values()))
    assert "Виктор" not in rec.proposed_series
