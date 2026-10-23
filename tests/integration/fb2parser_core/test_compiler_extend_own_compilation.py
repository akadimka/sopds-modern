"""Дописывание тома в сборник, собранный нашим же компилятором (2026-10-23).

_build_fb2 кладёт перед томами отдельный <body> с оглавлением, а
_extract_body_sections считал «нашим форматом» только случай «тел столько
же, сколько томов» — сборник уходил в разбор по <section> и резался по
главам. При автокомпиляции (каждая автосинхронизация вливает новый том в
существующий сборник) прежние тома превращались в «Содержание», «Глава 1»,
«Глава 2»…, а оглавление — в список глав. Теперь наш формат узнаётся по
<body id="vol_N">, оглавление томом не считается, название — без «N. ».
"""
import re
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import (
    CompilationBook,
    CompilationGroup,
    FB2CompilerService,
)
from fb2parser_core.passes.pass1_read_files import BookRecord


def _volume_body(n: int) -> str:
    return (f'<body><title><p>Том {n}</p></title>'
            f'<section><title><p>Глава 1</p></title><p>текст {n}.1</p></section>'
            f'<section><title><p>Глава 2</p></title><p>текст {n}.2</p></section></body>')


VOLUME_TITLES = {1: "Первый", 2: "Второй", 3: "Третий"}


def _volume_file(path: Path, n: int) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
        '<genre>sf</genre><author><first-name>Автор</first-name><last-name>Тестов</last-name></author>'
        f'<book-title>{VOLUME_TITLES[n]}</book-title><lang>ru</lang>'
        f'<sequence name="Серия" number="{n}"/></title-info></description>'
        f'{_volume_body(n)}</FictionBook>', encoding="utf-8")
    return path


def _book(path: Path, label: str, title: str) -> CompilationBook:
    record = BookRecord(file_path=path.name, file_title=title, metadata_authors="Автор Тестов",
                        proposed_author="Автор Тестов", author_source="metadata", metadata_series="Серия",
                        proposed_series="Серия", series_source="metadata", metadata_genre="sf",
                        series_number=label)
    return CompilationBook(record=record, abs_path=path, sort_key=(0, int(label.split("-")[0]), 0, 0),
                           sort_source="series_number",
                           order_ambiguous=False, volume_label=label)


def _volumes(xml: str):
    """[(id, заголовок тома)] по <body id="vol_N"> и оглавление."""
    bodies = re.findall(r'<body id="(vol_\d+)">\s*<title>\s*<p>(.*?)</p>', xml, re.S)
    toc = re.findall(r'<a l:href="#vol_\d+">(.*?)</a>', xml)
    return bodies, toc


@pytest.fixture
def service():
    return FB2CompilerService()


def test_own_compilation_is_read_back_volume_by_volume(service, tmp_path):
    xml = service._build_fb2("Автор Тестов", "Серия", "Дилогия", "sf",
                             [(VOLUME_TITLES[1], _volume_body(1)), (VOLUME_TITLES[2], _volume_body(2))])
    path = tmp_path / "Автор Тестов - Серия (Дилогия).fb2"
    path.write_text(xml, encoding="utf-8")
    sections = service._extract_body_sections(_book(path, "1-2", "Дилогия"), 1, 2)
    assert [(n, title) for n, title, _ in sections] == [(1, "Первый"), (2, "Второй")]
    assert all("Глава 1" in body and "Глава 2" in body for _, _, body in sections)   # главы — внутри тома


def test_adding_a_volume_keeps_previous_volumes_intact(service, tmp_path):
    pre = tmp_path / "Автор Тестов - Серия (Дилогия).fb2"
    pre.write_text(service._build_fb2("Автор Тестов", "Серия", "Дилогия", "sf",
                                      [(VOLUME_TITLES[1], _volume_body(1)), (VOLUME_TITLES[2], _volume_body(2))]),
                   encoding="utf-8")
    vol3 = _volume_file(tmp_path / "Автор Тестов - Серия 3. Третий.fb2", 3)
    group = CompilationGroup(author="Автор Тестов", series="Серия",
                             books=[_book(pre, "1-2", "Дилогия"), _book(vol3, "3", VOLUME_TITLES[3])],
                             order_determined=True, volume_range="1-3")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = service.compile_group(group, out_dir, delete_sources=False)
    assert result.success, result.error
    output = result.output_path
    bodies, toc = _volumes(output.read_text(encoding="utf-8"))
    assert [title for _, title in bodies] == ["1. Первый", "2. Второй", "3. Третий"]
    assert toc == ["1. Первый", "2. Второй", "3. Третий"]
    assert not any("Глава" in t or "Содержание" in t for t in toc)
