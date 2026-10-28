"""Сборники чужой сборки при синхронизации (docs/series-duplicates.md, вид 1;
сервер 2026-10-27, «Игрок, забравшийся на вершину»).

«Leach23 [Дмитрий Михалек]. Игрок, забравшийся на вершину 1-11» — серии в
<sequence> нет, она только в названии вместе с диапазоном томов. Пайплайн
серию не находил, компилятор книги без серии не видит, и сборник оставался
отдельной книгой рядом с нашей «Декалогией» и с более полными сборниками.

Теперь пайплайн берёт серию и диапазон из названия/имени файла
(RegenCSVService._postcheck_omnibus_series_from_title), а компилятор при
синхронизации (auto_compile_library — её вызывают ручная синхронизация и
автосинхронизация) оставляет самую полную по томам версию, при равенстве —
с лучшей разметкой глав и обложкой (решение пользователя 2026-10-27).
"""
import random
from pathlib import Path

import pytest

from fb2parser_core.auto_compile_service import auto_compile_library
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

pytestmark = pytest.mark.django_db
SERIES = "Игрок, забравшийся на вершину"
NS = 'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink"'


def _word(n: int) -> str:
    """Буквенное «слово» по числу: токенизатор считает его одним словом, как в книге."""
    letters = "абвгдежзиклмнопрстуфхцчшщэюя"
    out = ""
    while True:
        n, r = divmod(n, len(letters))
        out += letters[r]
        if not n:
            return out


def _text(volume: int, words: int = 3000) -> str:
    rng = random.Random(volume)
    return " ".join(_word(volume * 100_000 + rng.randint(0, 9999)) for _ in range(words))


def _volume_sections(volume: int, chapters: bool) -> str:
    text = _text(volume).split()
    parts = [" ".join(text[i:i + 500]) for i in range(0, len(text), 500)]
    if chapters:
        return "".join(f"<section><title><p>Книга {volume}. Глава {n}</p></title><p>{p}</p></section>"
                       for n, p in enumerate(parts, 1))
    return f"<section><p>{' '.join(parts)}</p></section>"


def _omnibus(path: Path, title: str, author: str, volumes, chapters=True, cover_bytes=0) -> Path:
    cover = binary = ""
    if cover_bytes:
        cover = '<coverpage><image l:href="#cover.jpg"/></coverpage>'
        binary = f'<binary id="cover.jpg" content-type="image/jpeg">{"A" * cover_bytes}</binary>'
    last, *first = author.split()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><FictionBook {NS}><description><title-info>'
        f'<genre>sf</genre><author><first-name>{" ".join(first)}</first-name><last-name>{last}</last-name></author>'
        f'<book-title>{title}</book-title>{cover}<lang>ru</lang></title-info></description>'
        f'<body>{"".join(_volume_sections(v, chapters) for v in volumes)}</body>{binary}</FictionBook>',
        encoding="utf-8")
    return path


def _our_compilation(path: Path, author: str, n: int) -> Path:
    bodies = [(f"Книга {v}", f"<body>{_volume_sections(v, True)}</body>") for v in range(1, n + 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FB2CompilerService()._build_fb2(author, SERIES, "Декалогия", "sf", bodies), encoding="utf-8")
    return path


def _sync_compile(library: Path, author_dir: Path):
    """Как после синхронизации: компиляция затронутой папки автора."""
    return auto_compile_library(str(library), config_path=_config_path(), filter_paths={str(author_dir)})


def test_series_and_range_come_from_the_title(tmp_path):
    author_dir = tmp_path / "Фантастика" / "Leach23"
    _omnibus(author_dir / f"Leach23 (Дмитрий Михалек). {SERIES} 1-11.fb2",
             f"Leach23 (Дмитрий Михалек). {SERIES} 1-11", "Leach23", range(1, 3))
    _omnibus(author_dir / "Антология классического детектива-15. Книги 1-15.fb2",
             "Антология классического детектива-15. Книги 1-15", "Leach23", range(1, 2))
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    by_name = {Path(r.file_path).name: r for r in records}
    omnibus = by_name[f"Leach23 (Дмитрий Михалек). {SERIES} 1-11.fb2"]
    assert (omnibus.proposed_series, omnibus.series_number) == (SERIES, "1-11")
    anthology = by_name["Антология классического детектива-15. Книги 1-15.fb2"]
    assert anthology.proposed_series != "Антология классического детектива-15. Книги"   # диапазон — про содержимое


def test_omnibus_next_to_our_compilation_is_removed_as_duplicate(tmp_path):
    author_dir = tmp_path / "Фантастика" / "Михалек Дмитрий"
    ours = _our_compilation(author_dir / SERIES / f"Михалек Дмитрий - {SERIES} (Декалогия).fb2", "Михалек Дмитрий", 10)
    part = _omnibus(author_dir / f"Leach23 [Дмитрий Михалек]. {SERIES} 6-10.fb2",
                    f"Leach23 [Дмитрий Михалек]. {SERIES} 6-10", "Михалек Дмитрий", range(6, 11))
    _sync_compile(tmp_path, author_dir)
    assert ours.exists() and not part.exists()


def test_most_complete_omnibus_is_kept(tmp_path):
    author_dir = tmp_path / "Фантастика" / "Leach23"
    files = {
        rng: _omnibus(author_dir / f"Leach23 [Дмитрий Михалек]. {SERIES} {rng}.fb2",
                      f"Leach23 [Дмитрий Михалек]. {SERIES} {rng}", "Leach23", range(lo, hi + 1))
        for rng, lo, hi in (("1-5", 1, 5), ("1-11", 1, 11), ("1-12", 1, 12))
    }
    _sync_compile(tmp_path, author_dir)
    assert not files["1-5"].exists() and not files["1-11"].exists()
    left = sorted(p.name for p in author_dir.rglob("*.fb2*"))
    assert len(left) == 1 and SERIES in left[0], left                 # 1-12, под именем серии


def test_equal_range_keeps_better_chapters_and_cover(tmp_path):
    author_dir = tmp_path / "Фантастика" / "Гаркушин Игорь"
    plain = _omnibus(author_dir / "Гаркушин Игорь. Наследник Арисара 1-3.fb2",
                     "Гаркушин Игорь. Наследник Арисара 1-3", "Гаркушин Игорь", range(1, 4),
                     chapters=False, cover_bytes=100)
    marked = _omnibus(author_dir / "Наследник Арисара (т. 1-3).fb2", "Наследник Арисара (т. 1-3)",
                      "Гаркушин Игорь", range(1, 4), chapters=True, cover_bytes=5000)
    _sync_compile(tmp_path, author_dir)
    left = list(author_dir.rglob("*.fb2*"))
    assert not plain.exists() and len(left) == 1
    assert "Книга 3. Глава 6" in left[0].read_text(encoding="utf-8")   # оставлена версия с главами


def test_large_texts_are_sampled_but_compared_fairly(tmp_path):
    """Большой текст (больше _SHINGLE_LARGE_WORDS слов) хранится выборкой —
    иначе проверка дублей на всей библиотеке падала с MemoryError (Test2,
    2026-10-27); сравнение с ним идёт в той же выборке, доля не занижается."""
    svc = FB2CompilerService()
    big = _omnibus(tmp_path / "big.fb2", "Сборник", "Автор Тестов", ())
    volumes = "".join(f"<section><p>{_text(v, 100_000)}</p></section>" for v in range(1, 5))
    big.write_text(big.read_text(encoding="utf-8").replace("<body></body>", f"<body>{volumes}</body>"),
                   encoding="utf-8")
    vol2 = _omnibus(tmp_path / "vol2.fb2", "Том 2", "Автор Тестов", ())
    vol2.write_text(vol2.read_text(encoding="utf-8").replace(
        "<body></body>", f"<body><section><p>{_text(2, 100_000)}</p></section></body>"), encoding="utf-8")
    other = _omnibus(tmp_path / "other.fb2", "Другая", "Автор Тестов", ())
    other.write_text(other.read_text(encoding="utf-8").replace(
        "<body></body>", f"<body><section><p>{_text(99, 100_000)}</p></section></body>"), encoding="utf-8")
    big_set = svc._text_shingles(big)
    full = FB2CompilerService()
    full._SHINGLE_LARGE_WORDS = 10 ** 9
    assert big_set.sampled and len(big_set) < len(full._text_shingles(big)) / 5   # прорежен
    assert not svc._text_shingles(vol2).sampled
    assert svc._shingle_share(svc._text_shingles(vol2), [big_set]) > 0.95
    assert svc._shingle_share(svc._text_shingles(other), [big_set]) < 0.05
    for i in range(svc._SHINGLE_CACHE_SIZE + 5):
        (tmp_path / f"x{i}.fb2").write_text(vol2.read_text(encoding="utf-8"), encoding="utf-8")
        svc._text_shingles(tmp_path / f"x{i}.fb2")
    assert len(svc._shingle_cache) == svc._SHINGLE_CACHE_SIZE


def test_sampled_fragments_are_the_same_in_every_process(tmp_path):
    """Выборка фрагментов большого текста не зависит от процесса: hash()
    строк рандомизирован (PYTHONHASHSEED), и решения «дубль или нет» около
    порога менялись от запуска к запуску (Test2, 2026-10-27)."""
    import os
    import subprocess
    import sys
    big = _omnibus(tmp_path / "big.fb2", "Сборник", "Автор Тестов", ())
    volumes = "".join(f"<section><p>{_text(v, 100_000)}</p></section>" for v in range(1, 5))
    big.write_text(big.read_text(encoding="utf-8").replace("<body></body>", f"<body>{volumes}</body>"),
                   encoding="utf-8")
    code = ("import sys; from pathlib import Path; from fb2parser_core.fb2_compiler import FB2CompilerService as S; "
            "s = S()._text_shingles(Path(sys.argv[1])); print(len(s), sum(s) % 1000003)")
    src = str(Path(__file__).resolve().parents[3] / "src")
    outs = {subprocess.run([sys.executable, "-c", code, str(big)], capture_output=True, text=True, cwd=src,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2")}
    assert len(outs) == 1 and next(iter(outs)).strip()
