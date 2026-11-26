"""Найдено пользователем в `regen_Test1.csv`, папка «Бушков Александр»
(2026-11-26):

- 19 рассказов из «Бушков Александр\\Рассказы» получили серию «Александра
  Бушкова». В метаданных — «Рассказы Александра Бушкова»; «Рассказы» —
  ключевое слово-франшиза (`series_universe_keywords`), и восстановление
  дуги из метаданных (баг №34) брало остаток. Остаток с фамилией автора —
  не дуга: серии нет.
- «Бушков Александр\\Сборники\\Румбы фантастики…», «Искатель…» — автором
  ставились все 6–13 авторов из метаданных (баг №133). Четыре автора и
  больше — сборник или журнал: автор остаётся из папки (решение
  пользователя). Соавторство двух-трёх авторов — как раньше.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>sf</genre>
{authors}
<book-title>{title}</book-title>{seq}
</title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _author(first, last):
    return f"<author><first-name>{first}</first-name><last-name>{last}</last-name></author>"


def _write(path, title, authors, seq=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(title=title, authors="".join(_author(*a) for a in authors), seq=seq),
                    encoding="utf-8")


def _records(tmp_path):
    return {r.file_path.split("\\")[-1]: r
            for r in RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)}


def test_stories_named_after_author_have_no_series(tmp_path):
    seq = '<sequence name="Рассказы Александра Бушкова"/>'
    for title in ("Брежнин луг", "Вечер для троих", "Волчье солнышко"):
        _write(tmp_path / "Бушков Александр" / "Рассказы" / f"{title}.fb2", title,
               [("Александр", "Бушков")], seq)
    recs = _records(tmp_path)
    assert {r.proposed_series for r in recs.values()} == {""}


def test_anthology_keeps_folder_author_coauthors_up_to_three(tmp_path):
    folder = tmp_path / "Бушков Александр" / "Сборники"
    _write(folder / "Румбы фантастики. 1988 год. Том I (сборник).fb2", "Румбы фантастики. 1988 год. Том I",
           [("Иван", "Ефремов"), ("Александр", "Бачило"), ("Александр", "Бушков"), ("Елена", "Грушко")])
    _write(folder / "Тройка (сборник).fb2", "Тройка",
           [("Александр", "Бушков"), ("Елена", "Грушко"), ("Иван", "Ефремов")])
    recs = _records(tmp_path)
    assert recs["Румбы фантастики. 1988 год. Том I (сборник).fb2"].proposed_author == "Бушков Александр"
    assert recs["Тройка (сборник).fb2"].proposed_author == "Бушков Александр, Грушко Елена, Ефремов Иван"
