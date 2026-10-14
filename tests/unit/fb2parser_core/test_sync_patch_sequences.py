"""`SynchronizationService._patch_fb2_tags()` и <sequence>/<book-title> — баг №148.

- Вложенные <sequence> («<sequence name="А"><sequence name="Б"/></sequence>»):
  заменялся только открывающий тег, оставался лишний </sequence> — файл в
  библиотеке становился некорректным XML (68 таких было в рабочей библиотеке).
- Номер тома: сохранялся номер ПЕРВОЙ исходной <sequence> (часто про другую
  подсерию: «Хроники Академии. Книга I» — том 8 «Орудий Смерти», но number="1"),
  и метаданные в библиотеке спорили с «т. 8» в имени файла.
- Подцикл «зонтика»: номер подцикла пишется отдельной <sequence name="Корень">,
  чтобы пережить переименование файла.
- <book-title>: «Серия N. Название» из имени файла подменяло настоящее название.
"""
import re

from lxml import etree

from fb2parser_core.logger import Logger
from fb2parser_core.synchronization import SynchronizationService

NESTED = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author><first-name>Кассандра</first-name><last-name>Клэр</last-name></author>
<book-title>Хроники Академии. Книга I</book-title>
<sequence name="Сумеречные охотники"><sequence name="Орудия Смерти"><sequence name="Хроники Академии" number="1"/></sequence></sequence>
<sequence name="Миры Кассандры Клэр"/>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _service():
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.logger = Logger()
    svc.log_callback = None
    svc.stats = {}
    return svc


def _patch(tmp_path, text, **kw):
    path = tmp_path / "book.fb2"
    path.write_text(text, encoding="utf-8")
    _service()._patch_fb2_tags(path, None, kw.pop("series"), kw.pop("title", None), **kw)
    data = path.read_bytes()
    etree.fromstring(data)  # корректный XML, иначе исключение
    return re.findall(r'<sequence[^>]*>', data.decode("utf-8"))


def test_nested_sequences_stay_well_formed_and_number_is_the_volume(tmp_path):
    seqs = _patch(tmp_path, NESTED, series="Сумеречные охотники\\Орудия Смерти", series_number="8")
    assert seqs[0] == '<sequence name="Сумеречные охотники\\Орудия Смерти" number="8"/>'
    assert '<sequence name="Миры Кассандры Клэр"/>' in seqs  # остальные — как были


def test_umbrella_cycle_number_is_written_as_root_sequence(tmp_path):
    flat = NESTED.replace(
        '<sequence name="Сумеречные охотники"><sequence name="Орудия Смерти"><sequence name="Хроники Академии" '
        'number="1"/></sequence></sequence>',
        '<sequence name="Адские механизмы (АСТ)" number="1"/>\n<sequence name="Сумеречные охотники"/>')
    seqs = _patch(tmp_path, flat, series="Сумеречные охотники\\Адские механизмы", series_number="2",
                  root_sequence=("Сумеречные охотники", 1))
    assert seqs[:2] == ['<sequence name="Сумеречные охотники\\Адские механизмы" number="2"/>',
                        '<sequence name="Сумеречные охотники" number="1"/>']
    assert sum('name="Сумеречные охотники"' in s for s in seqs) == 1  # старый корень без номера убран


def test_without_volume_number_the_original_number_is_kept(tmp_path):
    flat = NESTED.replace(NESTED[NESTED.index('<sequence name="Сумеречные'):NESTED.index('<sequence name="Миры')],
                          '<sequence name="Старая" number="5"/>\n')
    assert _patch(tmp_path, flat, series="Новая")[0] == '<sequence name="Новая" number="5"/>'


def test_title_with_series_prefix_is_not_a_new_title():
    s = SynchronizationService
    assert s._title_with_series_prefix("Власть пса", "Власть пса 1. Власть пса")
    assert s._title_with_series_prefix("Механический ангел",
                                       "Сумеречные охотники 1. Адские механизмы 1. Механический ангел")
    assert not s._title_with_series_prefix("Город стекла", "Город костей")
    assert s._strip_series_prefix("Сумеречные охотники 1. Адские механизмы 2. Механическая принц",
                                  "Сумеречные охотники\\Адские механизмы") == "Механическая принц"
    assert s._strip_series_prefix("Тёмные искусства 3. Королева", "Темные искусства") == "Королева"
    assert s._strip_series_prefix("Город 1. Город", "Деревня") == "Город 1. Город"  # чужая серия — как есть


def test_compiler_reads_cycle_number_from_library_metadata(tmp_path):
    """В библиотеке файлы переименованы («т. 1 …», без «Корень N.»), номер
    подцикла — только в <sequence name="Корень" number="N">: сборник всё равно
    «Сумеречные охотники 1. Адские механизмы»."""
    from types import SimpleNamespace

    from fb2parser_core.fb2_compiler import FB2CompilerService

    books = []
    old_seqs = NESTED[NESTED.index('<sequence name="Сумеречные'):NESTED.index('</title-info>')]
    for i in (1, 2, 3):
        p = tmp_path / f"т. {i} Книга.fb2"
        p.write_text(NESTED.replace(old_seqs,
                                    f'<sequence name="Сумеречные охотники\\Адские механизмы" number="{i}"/>\n'
                                    '<sequence name="Сумеречные охотники" number="1"/>\n'), encoding="utf-8")
        books.append(SimpleNamespace(abs_path=p, record=SimpleNamespace(series_number_source="metadata")))
    group = SimpleNamespace(books=books, series="Сумеречные охотники\\Адские механизмы", display_root="")
    assert FB2CompilerService._group_series_for_naming(group) == "Сумеречные охотники\\1. Адские механизмы"

    # Номер есть не у всех книг — не выдумываем
    books[2].abs_path.write_text(NESTED, encoding="utf-8")
    assert FB2CompilerService._group_series_for_naming(group) == "Сумеречные охотники\\Адские механизмы"
