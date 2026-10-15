"""Таблица нормализации показывает номер подцикла «зонтика», как компиляция
(баг №155): «Сумеречные охотники 1\\Адские механизмы» — номер места подцикла
в главной серии стоит на её стороне черты. В CSV номера нет —
`proposed_series` остаётся «Корень\\Подцикл» (ключ группировки и путь в
библиотеке); номер считается по именам файлов тем же
`evidence.umbrella_cycle_number`, что и в компиляторе."""
import pytest
from django.test import RequestFactory

from fb2parser_core.evidence import UMBRELLA_ARC_SN_SOURCE, umbrella_cycle_number
from fb2parser_web.views import norm_job, normalize_table

ROOT = "Сумеречные охотники"


def _rec(name, series, source=UMBRELLA_ARC_SN_SOURCE, number="1"):
    return {"file_path": f"Серия - «Миры Кассандры Клэр»\\Клэр Кассандра - {name}.fb2",
            "proposed_author": "Клэр Кассандра", "proposed_series": series,
            "series_number": number, "series_number_source": source}


@pytest.fixture(autouse=True)
def _reset_norm_job():
    norm_job.reset()
    yield
    norm_job.reset()


def _table(admin_user, records):
    norm_job.update(records=records, records_total=len(records))
    request = RequestFactory().get("/fb2parser/normalize/table/")
    request.user = admin_user
    return normalize_table(request).content.decode("utf-8")


def test_subcycle_number_shown_but_csv_value_unchanged(admin_user):
    records = [
        _rec("Сумеречные охотники 1. Адские механизмы 1. Механический ангел", f"{ROOT}\\Адские механизмы"),
        _rec("Сумеречные охотники 1. Адские механизмы 2. Механический принц", f"{ROOT}\\Адские механизмы", number="2"),
        _rec("Сумеречные охотники 2. Орудия Смерти 1. Город костей", f"{ROOT}\\Орудия Смерти"),
    ]
    html = _table(admin_user, records)
    # номер — на стороне главной серии: «Сумеречные охотники 1\Адские механизмы»
    assert f'{ROOT} <span class="nt-cycle"' in html
    assert '>1</span>\\Адские механизмы' in html
    assert '>2</span>\\Орудия Смерти' in html
    assert norm_job.get()["records"][0]["proposed_series"] == f"{ROOT}\\Адские механизмы"
    assert "cycle_n" not in norm_job.get()["records"][0]


def test_no_number_when_volumes_disagree_or_not_umbrella(admin_user):
    records = [
        _rec("Сумеречные охотники 1. Адские механизмы 1. Механический ангел", f"{ROOT}\\Адские механизмы"),
        _rec("Сумеречные охотники 3. Адские механизмы 2. Механический принц", f"{ROOT}\\Адские механизмы"),
        _rec("Мир Вальдиры 1. Кроу", "Мир Вальдиры\\Кроу", source="filename"),
    ]
    html = _table(admin_user, records)
    assert "nt-cycle" not in html
    assert f"{ROOT}\\Адские механизмы" in html


def test_umbrella_cycle_number():
    assert umbrella_cycle_number(["Клэр - Сумерéчные охотники 4. Последние часы 1.fb2",
                                  "Клэр - Сумеречные охотники 4. Последние часы 2.fb2"], "Сумеречные охотники") is None
    assert umbrella_cycle_number(["Клэр - Тёмные 4. Часы 1", "Клэр - Темные 4. Часы 2"], "Тёмные") == 4
    assert umbrella_cycle_number(["Клэр - Тёмные 4. Часы 1", "Клэр - Часы 2"], "Тёмные") is None
