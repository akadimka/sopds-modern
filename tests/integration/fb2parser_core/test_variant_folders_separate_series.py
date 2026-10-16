"""Подпапки-варианты — отдельная серия (баг №157).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md), решение пользователя
2026-10-14: «Перезагрузка», «Версия 2.0», «альтернативное продолжение» —
отдельная серия со своим сборником, а не тома основной. Раньше такие
папки сливались с основной серией, номера томов сталкивались:
- вложенная папка с ключевым словом варианта (`variant_folder_keywords`:
  «Свободные агенты/Версия 2.0», «Перешагнуть пропасть/Возвращение
  (альтернативное продолжение)») не образовывала уровня серии;
- соседняя папка с расширенным именем сводилась к базовому:
  «Избранный Богами (Перезагрузка)» → «Избранный Богами», «Старатели.
  Новая история» → «Старатели», «Сопряжение. Чернильный маг» →
  «Сопряжение» (pass4 счёл «Чернильный маг» обрывком);
- заодно ключевое слово в имени самой серии прямо под папкой автора
  («Проект Альтернативная Земля», Князев) выбрасывало серию целиком.

Форматные варианты («СИ», «ЛП», «самиздат», «черновик» — те же книги в
другом издании) — по-прежнему серия родителя.
"""
import pytest

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>sf</genre>
<author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title>{seq}
</title-info></description>
<body><title><p>{title}</p></title><section><p>Текст книги {title}.</p></section></body>
</FictionBook>
"""

ROOT = ("Серия - «LitRPG»", "Hеофициальная серия книг (разные издательства)")

LAYOUT = {
    # (папка автора, имя, фамилия): {относительная папка: [(файл, название, серия в мете)]}
    ("Горностаев-Василий", "Василий", "Горностаев"): {
        "Свободные агенты": [("1. Ролевой синдром.fb2", "Ролевой синдром", "Свободные агенты"),
                             ("2. Рассвет - твой враг.fb2", "Рассвет - твой враг", "Свободные агенты"),
                             ("3. Радиостанция паники.fb2", "Радиостанция паники", "Свободные агенты")],
        "Свободные агенты/Версия 2.0": [
            ("1. Ролевой синдром Ребилд.fb2", "Ролевой синдром: Ребилд", "Свободные агенты"),
            ("2. Радикальные меры.fb2", "Радикальные меры", "Свободные агенты"),
            ("3. Рассвет - твой враг Ребилд.fb2", "Рассвет - твой враг: Ребилд", "Свободные агенты")],
    },
    ("Муравьев-Константин", "Константин", "Муравьев"): {
        "Перешагнуть пропасть": [("1. Перешагнуть пропасть.fb2", "Перешагнуть пропасть", "Перешагнуть пропасть"),
                                 ("2. День решает все.fb2", "День решает все", "Перешагнуть пропасть"),
                                 ("3. Шаг в бездну.fb2", "Шаг в бездну", "Перешагнуть пропасть")],
        "Перешагнуть пропасть/Возвращение (альтернативное продолжение)": [
            ("1. Возвращение.fb2", "Возвращение", "")],
    },
    ("Лей-Влад", "Влад", "Лей"): {
        "Старатели": [("01. Старатель.fb2", "Старатель", "Старатели"),
                      ("02. Старатель 2.fb2", "Старатель 2", "Старатели")],
        "Старатели. Новая история": [("01. Старатели.fb2", "Старатели. Новая история", "")],
    },
    ("Roy", "", "Roy"): {
        "Избранный богами": [("1. Том 1.fb2", "Избранный богами. Том 1", "Избранный богами"),
                             ("2. Том 2.fb2", "Избранный богами. Том 2", "Избранный богами")],
        "Избранный Богами (Перезагрузка)": [("01. Том 1.fb2", "Избранный богами. Перезагрузка", "")],
    },
    ("Поляков-Эдуард", "Эдуард", "Поляков"): {
        "Сопряжение": [("1. ЗИЛ-1.fb2", "ЗИЛ-1", "Сопряжение"), ("2. ЗИЛ-2.fb2", "ЗИЛ-2", "Сопряжение")],
        "Сопряжение. Чернильный маг": [("1. Чернильный маг.fb2", "Чернильный маг", ""),
                                       ("2. Сопряжение. Чернильный маг 2.fb2", "Чернильный маг 2", "")],
    },
    ("Князев-Павел (Silvan)", "Павел", "Князев"): {
        "Проект Альтернативная Земля": [("1. Альтернативная Земля.fb2", "Альтернативная Земля", ""),
                                        ("2. Альтернативная Земля 2.fb2", "Альтернативная Земля 2", "")],
    },
    ("Сидоров-Пётр", "Пётр", "Сидоров"): {
        "Дорога": [("1. Дорога.fb2", "Дорога", "Дорога"), ("2. Путь.fb2", "Путь", "Дорога")],
        "Дорога/СИ": [("1. Дорога.fb2", "Дорога (СИ)", "Дорога"), ("2. Путь.fb2", "Путь (СИ)", "Дорога")],
    },
}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("variants").joinpath(*ROOT)
    for (author_dir, first, last), folders in LAYOUT.items():
        for folder, books in folders.items():
            d = root / author_dir / folder
            d.mkdir(parents=True, exist_ok=True)
            for name, title, meta_series in books:
                seq = f'<sequence name="{meta_series}"/>' if meta_series else ""
                (d / name).write_text(_FB2.format(first=first, last=last, title=title, seq=seq), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def records(library, tmp_path_factory):
    out = tmp_path_factory.mktemp("variants_out") / "regen.csv"
    return RegenCSVService(_config_path()).generate_csv(str(library), output_csv_path=str(out))


def _series(records, *suffix):
    tail = "/".join(suffix)
    found = [r for r in records if r.file_path.replace("\\", "/").endswith(tail)]
    assert len(found) == 1, (tail, [r.file_path for r in records])
    return found[0].proposed_series


@pytest.mark.parametrize("parent, variant, parent_file, variant_file", [
    ("Горностаев-Василий/Свободные агенты", "Горностаев-Василий/Свободные агенты/Версия 2.0",
     "1. Ролевой синдром.fb2", "1. Ролевой синдром Ребилд.fb2"),
    ("Муравьев-Константин/Перешагнуть пропасть",
     "Муравьев-Константин/Перешагнуть пропасть/Возвращение (альтернативное продолжение)",
     "1. Перешагнуть пропасть.fb2", "1. Возвращение.fb2"),
    ("Лей-Влад/Старатели", "Лей-Влад/Старатели. Новая история", "01. Старатель.fb2", "01. Старатели.fb2"),
    ("Roy/Избранный богами", "Roy/Избранный Богами (Перезагрузка)", "1. Том 1.fb2", "01. Том 1.fb2"),
    ("Поляков-Эдуард/Сопряжение", "Поляков-Эдуард/Сопряжение. Чернильный маг",
     "1. ЗИЛ-1.fb2", "1. Чернильный маг.fb2"),
])
def test_variant_is_its_own_series(records, parent, variant, parent_file, variant_file):
    base = _series(records, parent, parent_file)
    other = _series(records, variant, variant_file)
    assert base and other and other != base, (base, other)


def test_variant_keyword_in_the_series_folder_itself_keeps_the_series(records):
    assert _series(records, "Князев-Павел (Silvan)/Проект Альтернативная Земля",
                   "1. Альтернативная Земля.fb2") == "Проект Альтернативная Земля"


def test_format_variant_still_belongs_to_the_parent_series(records):
    assert _series(records, "Сидоров-Пётр/Дорога/СИ", "1. Дорога.fb2") == "Дорога"


def test_variant_compiles_separately(records, library):
    groups = FB2CompilerService().find_groups(records, library)
    by_series = {}
    for g in groups:
        by_series.setdefault((g.author, g.series), []).extend(b.abs_path.name for b in g.books)
    gorn = {k: v for k, v in by_series.items() if k[0].startswith("Горностаев")}
    assert len(gorn) == 2, gorn
    assert not any(g.duplicate_paths or g.excluded_paths for g in groups if g.author.startswith("Горностаев"))
