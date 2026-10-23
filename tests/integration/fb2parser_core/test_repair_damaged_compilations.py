"""scripts/repair_damaged_compilations.py (docs/damaged-compilations.md).

Порча — настоящая: тот же компилятор, которому скрыт признак нашего формата
(<body id="vol_N">), разбирает сборник по <section>, как до v2.35.001.
Починка должна вернуть тома с прежними названиями, главы — внутрь томов и
с их названиями, текст и картинки — без изменений.
"""
import importlib.util
import re
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import (
    CompilationBook,
    CompilationGroup,
    FB2CompilerService,
)
from fb2parser_core.passes.pass1_read_files import BookRecord

_spec = importlib.util.spec_from_file_location(
    "repair_damaged_compilations",
    Path(__file__).resolve().parents[3] / "scripts" / "repair_damaged_compilations.py")
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)

TITLES = {1: "Первый", 2: "Второй", 3: "Третий"}


def _volume_file(folder: Path, n: int) -> Path:
    path = folder / f"Автор Тестов - Серия {n}. {TITLES[n]}.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
        '<genre>sf</genre><author><first-name>Автор</first-name><last-name>Тестов</last-name></author>'
        f'<book-title>{TITLES[n]}</book-title>'
        f'<coverpage><image l:href="#cover{n}.jpg"/></coverpage><lang>ru</lang>'
        f'<sequence name="Серия" number="{n}"/></title-info></description>'
        f'<body><title><p>{TITLES[n]}</p></title>'
        f'<section><title><p>Глава 1</p></title><p>текст {n}.1</p>'
        f'<section><title><p>Сцена</p></title><p>сцена {n}.1</p></section></section>'
        f'<section><title><p>Глава 2</p></title><p>текст {n}.2</p>'
        + (f'<image l:href="#pic{n}.jpg"/>' if n == 2 else '') +
        '</section></body>'
        f'<binary id="cover{n}.jpg" content-type="image/jpeg">Y292ZXI{n}</binary>'
        + (f'<binary id="pic{n}.jpg" content-type="image/jpeg">cGlj</binary>' if n == 2 else '') +
        '</FictionBook>', encoding="utf-8")
    return path


def _book(path: Path, label: str, title: str) -> CompilationBook:
    record = BookRecord(file_path=path.name, file_title=title, metadata_authors="Автор Тестов",
                        proposed_author="Автор Тестов", author_source="metadata", metadata_series="Серия",
                        proposed_series="Серия", series_source="metadata", metadata_genre="sf",
                        series_number=label)
    return CompilationBook(record=record, abs_path=path, sort_key=(0, int(label.split("-")[0]), 0, 0),
                           sort_source="series_number", order_ambiguous=False, volume_label=label)


def _compile(service, books, volume_range, out_dir):
    group = CompilationGroup(author="Автор Тестов", series="Серия", books=books,
                             order_determined=True, volume_range=volume_range)
    out_dir.mkdir()
    result = service.compile_group(group, out_dir, delete_sources=False)
    assert result.success, result.error
    return result.output_path


def _titles(xml: str):
    return [repair.Body(m.group(2)).title for m in repair._vol_bodies(xml)]


@pytest.fixture
def damaged(tmp_path, monkeypatch):
    """(испорченный сборник т. 1-3, чистый сборник т. 1-3 из тех же томов)."""
    service = FB2CompilerService()
    src = tmp_path / "src"
    src.mkdir()
    vols = {n: _volume_file(src, n) for n in TITLES}
    clean = _compile(service, [_book(vols[n], str(n), TITLES[n]) for n in TITLES], "1-3", tmp_path / "clean")
    pre = _compile(service, [_book(vols[1], "1", TITLES[1]), _book(vols[2], "2", TITLES[2])], "1-2",
                   tmp_path / "pre")

    read = FB2CompilerService._read_file_text
    monkeypatch.setattr(FB2CompilerService, "_read_file_text",
                        lambda self, path: read(self, path).replace('id="vol_', 'id="old_vol_'))
    bad = _compile(service, [_book(pre, "1-2", "Дилогия"), _book(vols[3], "3", TITLES[3])], "1-3",
                   tmp_path / "bad")
    return bad.read_text(encoding="utf-8"), clean.read_text(encoding="utf-8")


def test_damage_is_the_real_one(damaged):
    bad, _ = damaged
    titles = _titles(bad)
    assert "Содержание" in titles and titles.count("Глава 1") == 2      # как в библиотеке
    assert titles[-1] == "Третий"


def test_repair_restores_volumes_chapters_and_keeps_text(damaged):
    bad, clean = damaged
    volumes, why = repair.plan(bad)
    assert why is None
    assert [v.title for v in volumes] == ["Первый", "Второй", "Третий"]
    assert [v.rebuilt for v in volumes] == [True, True, False]

    fixed = repair.rebuild(bad, volumes)
    repair.verify(bad, fixed)
    assert _titles(fixed) == _titles(clean) == ["Первый", "Второй", "Третий"]
    toc = re.findall(r'<a l:href="#vol_\d+">(.*?)</a>', fixed)
    assert toc == ["1. Первый", "2. Второй", "3. Третий"]
    # главы — внутри томов, с названиями; вложенный раздел не тронут
    vol1 = repair._vol_bodies(fixed)[0].group(2)
    assert re.findall(r'<title>\s*<p>(.*?)</p>', vol1) == ["1. Первый", "Глава 1", "Сцена", "Глава 2"]
    # текст по томам тот же, что в сборнике из исходных томов
    for got, want in zip(repair._vol_bodies(fixed), repair._vol_bodies(clean)):
        assert repair.PARA.findall(re.sub(r'<title>.*?</title>', '', got.group(2), flags=re.S)) == \
            repair.PARA.findall(re.sub(r'<title>.*?</title>', '', want.group(2), flags=re.S))
    # у тома одна обложка, картинка главы не продублирована
    vol2 = repair._vol_bodies(fixed)[1].group(2)
    assert vol2.count("<image") == 2
    assert repair.plan(fixed) == (None, "вложенных оглавлений 0")      # повторная починка не нужна


def test_lost_text_is_not_repaired(damaged):
    """Тома без текста (только обложка) — не восстанавливать: нужен исходник."""
    bad, _ = damaged
    gutted = re.sub(r'(<body id="vol_\d+">\s*<title>\s*<p>\d+\. Глава [12]</p>\s*</title>).*?</body>',
                    r'\1</body>', bad, flags=re.S)
    volumes, why = repair.plan(gutted)
    assert volumes is None and "нет текста" in why


def test_write_keeps_zip_inner_name(tmp_path, damaged):
    import zipfile
    bad, _ = damaged
    path = tmp_path / "Автор Тестов - Серия (Трилогия).fb2.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Автор Тестов - Серия (Трилогия).fb2", bad.encode("utf-8"))
    volumes, _ = repair.plan(bad)
    repair.write_fb2(str(path), repair.rebuild(bad, volumes))
    with zipfile.ZipFile(path) as z:
        assert z.namelist() == ["Автор Тестов - Серия (Трилогия).fb2"]
    assert _titles(repair.read_fb2(str(path))) == ["Первый", "Второй", "Третий"]
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []
