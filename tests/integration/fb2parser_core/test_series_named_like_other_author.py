"""Серия с именем другого автора коллекции — docs/quality-roadmap.md, баг №170.

`_postcheck_clear_author_as_series` стирала серию, совпавшую с именем
ЛЮБОЙ папки автора в прогоне. В «СЕРИЯ.LitRPG» есть авторы-псевдонимы
«Дикарь», «Сумрак», «Мир» — у Усманова пропала серия «Дикарь» (11 томов),
у Ефремова «Сумрак» (7), у Алексеева и shellina «Мир». Прогон одной папки
автора серию сохранял — терялась только на полном.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author>{author}</author>
<book-title>{title}</book-title>
</title-info>
</description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_COLLECTION = ("СЕРИЯ.LitRPG", "Hеофициальная серия книг (разные издательства)")


def _write(root, *parts, author):
    path = root.joinpath(*_COLLECTION, *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(author=author, title=path.stem[3:]), encoding="utf-8")


def _records(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").split("/", 2)[2]: r for r in records}


def test_series_named_like_a_pseudonym_author_is_kept(tmp_path):
    usmanov = "<first-name>Хайдарали</first-name><last-name>Усманов</last-name>"
    _write(tmp_path, "Усманов-Хайдарали", "Дикарь", "1. Идущий.fb2", author=usmanov)
    _write(tmp_path, "Усманов-Хайдарали", "Дикарь", "2. Бег по кругу.fb2", author=usmanov)
    _write(tmp_path, "Дикарь", "Готика", "1. Долгий путь в Миртану.fb2", author="<nickname>Дикарь</nickname>")

    recs = _records(tmp_path)
    for name in ("Усманов-Хайдарали/Дикарь/1. Идущий.fb2", "Усманов-Хайдарали/Дикарь/2. Бег по кругу.fb2"):
        assert recs[name].proposed_series == "Дикарь", (name, recs[name].proposed_series)
    gothic = recs["Дикарь/Готика/1. Долгий путь в Миртану.fb2"]
    assert (gothic.proposed_author, gothic.proposed_series) == ("Дикарь", "Готика")


def test_own_author_folder_still_not_a_series(tmp_path):
    """Файл прямо в папке автора — серией папка автора не становится (как раньше)."""
    _write(tmp_path, "Сумрак", "Апокриф создателя.fb2", author="<nickname>Сумрак</nickname>")
    _write(tmp_path, "Сумрак", "Апокриф создателя 2.fb2", author="<nickname>Сумрак</nickname>")
    recs = _records(tmp_path)
    assert all(r.proposed_series.lower() != "сумрак" for r in recs.values()), \
        [(k, r.proposed_series) for k, r in recs.items()]
