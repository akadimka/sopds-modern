"""Дубли и «хвосты» серий рядом с нашими компиляциями (docs/series-duplicates.md).

Только чтение: каталог и файлы библиотеки, ничего не меняет. Для каждой
серии (автор = папка автора, серия по названию), где есть наша компиляция
«Автор - Серия (Трилогия).fb2», — какие ещё книги той же серии лежат рядом
и что с каждой предлагается сделать. Отдельно — серии, разложенные по
нескольким жанрам, и пары «псевдоним — настоящее имя» из названий книг
(«Leach23 [Дмитрий Михалек]. …»).

Классификация эвристическая — это отчёт для решения, а не основание для
удаления: чистка (шаг 3 плана) — только после пробного прогона и «да».
"""
from __future__ import annotations

import os
import re
import zipfile
from collections import Counter, defaultdict
from typing import Callable, Optional

from django.utils.translation import gettext_lazy as _lazy

SUFFIX_WORDS = {"дилогия": 2, "трилогия": 3, "тетралогия": 4, "пенталогия": 5, "гексалогия": 6,
                "гепталогия": 7, "септология": 7, "октология": 8, "нонология": 9, "ноналогия": 9,
                "декалогия": 10}
OUR_SUFFIX = re.compile(r"\((" + "|".join(SUFFIX_WORDS) + r"|в (\d+) книгах)\)", re.I)
OMNIBUS = re.compile(r"(\d{1,3}\s*[-–—]\s*\d{1,3}|компиляц|сборник|омнибус|в \d+ книгах|весь цикл|цикл\s*«|"
                     r"дилоги|трилоги|тетралоги|пенталоги|гексалоги|гепталоги|октологи|ноналоги|декалоги)", re.I)
SHORT = re.compile(r"(рассказ|отрывок|фрагмент|ознакомит|глоссари|черновик|\bгл\.\s*\d|глав[аы]\s*\d)", re.I)
NUMBER_IN_TITLE = re.compile(r"(\bкн(?:ига)?\.?\s*\d{1,3}\b|#\s*\d{1,3}\b|^\d{1,3}\.?\s+\S|\S\s+\d{1,3}\s*$|"
                             r"\.\s*\d{1,3}\.\s)", re.I)
OUTSIDE_NUMBERING = re.compile(r"\bт\.\s*0\b", re.I)
PSEUDONYM = re.compile(r"^\s*([^.\[\]()]{2,40}?)\s*[\[(]([^\[\]()]{3,60})[\])]\s*\.\s")
SMALL_CHARS = 60_000
OMNIBUS_FACTOR = 1.8          # во столько раз больше среднего тома компиляции — целый цикл

KINDS = {
    "in_compilation": (_lazy("Already inside the compilation"), _lazy("duplicate — remove")),
    "omnibus": (_lazy("Third-party omnibus"), _lazy("keep the most complete version")),
    "other_genre": (_lazy("In another genre"), _lazy("move to the series' majority genre")),
    "probable_duplicate": (_lazy("Volume number within the compilation"), _lazy("probably a duplicate — check")),
    "beyond": (_lazy("Volume number beyond the compilation"), _lazy("append to the compilation")),
    "outside_numbering": (_lazy("Outside the numbering (vol. 0)"), _lazy("keep separately")),
    "small": (_lazy("Short work (story, glossary)"), _lazy("keep separately")),
    "number_in_title": (_lazy("Volume number only in the title"), _lazy("append once the number is recognised")),
    # решение пользователя 2026-10-27: том без номера остаётся рядом, в компиляцию не ставится
    "unnumbered": (_lazy("Volume without a number"), _lazy("keep next to the compilation")),
}
ISSUES = {
    "no_volume_marks": _lazy("no volume marks inside (old format or a third-party omnibus)"),
    "suffix_mismatch": _lazy("the suffix does not match the number of volumes inside"),
    "author_as_volume_title": _lazy("volume titles are the author's name"),
}


def norm(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", (s or "").lower().replace("ё", "е"))
    return re.sub(r"[^\w]+", " ", s).strip()


def _parts(book) -> list:
    return re.split(r"[\\/]", book.path or "")


def genre_of(book) -> str:
    return _parts(book)[0]


def author_of(book) -> str:
    parts = _parts(book)
    return parts[1] if len(parts) > 1 else ""


def read_book(root: str, book) -> Optional[str]:
    """Текст FB2 книги каталога (zip: path — архив, filename — файл в нём)."""
    path = os.path.join(root, book.path)
    try:
        if path.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                raw = z.read(book.filename)
        else:
            with open(os.path.join(path, book.filename), "rb") as f:
                raw = f.read()
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    enc = re.search(rb'encoding="([^"]+)"', raw[:200])
    try:
        return raw.decode(enc.group(1).decode() if enc else "utf-8", "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def volume_titles(xml: str) -> list:
    """Названия томов нашей компиляции (<body id="vol_N"><title><p>N. Название)."""
    titles = re.findall(r'<body[^>]*\bid="vol_\d+"[^>]*>\s*<title>\s*<p>(.*?)</p>', xml or "", re.S)
    return [re.sub(r"^\d+\.\s*", "", re.sub(r"<[^>]+>", "", t)).strip() for t in titles]


def text_chars(xml: str) -> int:
    body = re.sub(r"<binary.*?</binary>", "", xml or "", flags=re.S)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


def suffix_count(book) -> Optional[int]:
    m = OUR_SUFFIX.search(book.title or "") or OUR_SUFFIX.search(book.filename or "")
    if not m:
        return None
    return int(m.group(2)) if m.group(2) else SUFFIX_WORDS[m.group(1).lower()]


def classify(book, xml: Optional[str], comp, comp_vols: list, comp_avg_chars: int) -> str:
    """Вид книги рядом с компиляцией серии (ключ KINDS)."""
    title = book.title or ""
    t = norm(title)
    vol_norms = [norm(v) for v in comp_vols]
    if t and any(t == v or (len(t) > 6 and len(v) > 6 and (t in v or v in t)) for v in vol_norms):
        return "in_compilation"
    chars = text_chars(xml) if xml else 0
    if OMNIBUS.search(title) or OMNIBUS.search(book.filename or "") or (
            comp_avg_chars and chars > OMNIBUS_FACTOR * comp_avg_chars):
        return "omnibus"
    if genre_of(book) != genre_of(comp):
        return "other_genre"
    numbers = [x.ser_no for x in book.bseries_set.all() if x.ser_no]
    if numbers:
        covered = len(comp_vols) or suffix_count(comp) or 0
        return "probable_duplicate" if max(numbers) <= covered else "beyond"
    if OUTSIDE_NUMBERING.search(book.filename or ""):
        return "outside_numbering"
    if SHORT.search(title) or (xml and chars < SMALL_CHARS):
        return "small"
    if NUMBER_IN_TITLE.search(title):
        return "number_in_title"
    return "unnumbered"


def compilation_issues(comp, vols: list) -> list:
    issues = []
    if not vols:
        issues.append("no_volume_marks")
    else:
        expected = suffix_count(comp)
        if expected and expected != len(vols):
            issues.append("suffix_mismatch")
        author = norm(author_of(comp))
        if author and sum(norm(v) == author for v in vols) > len(vols) / 2:
            issues.append("author_as_volume_title")
    return issues


def _book_row(book) -> dict:
    return {"id": book.id, "path": f"{book.path}/{book.filename}" if not book.path.lower().endswith(".zip")
            else book.path, "title": book.title or "", "genre": genre_of(book),
            "added": book.registerdate}


def check(root: str, books=None, progress: Optional[Callable[[int, int, str], None]] = None) -> dict:
    """Отчёт по библиотеке. `books` — книги каталога (по умолчанию все
    доступные); `progress(processed, total, current)` — для страницы."""
    from opds_catalog.models import Book

    if books is None:
        books = Book.objects.filter(avail__gt=0)
    books = list(books.prefetch_related("series", "bseries_set"))

    groups = defaultdict(list)                  # (серия, автор) → книги
    series_name = {}
    for b in books:
        for s in b.series.all():
            key = (norm(s.ser), norm(author_of(b)))
            groups[key].append(b)
            series_name.setdefault(key, s.ser)

    with_comp = [(k, g) for k, g in groups.items() if any(suffix_count(b) for b in g)]
    result, kind_counts = [], Counter()
    for done, (key, group) in enumerate(with_comp, 1):
        comps = [b for b in group if suffix_count(b)]
        comp = max(comps, key=lambda b: b.registerdate)
        if progress:
            progress(done, len(with_comp), f"{author_of(comp)} / {series_name[key]}")
        extras_books = [b for b in group if b not in comps]
        extra_comps = [b for b in comps if b is not comp]           # вторая «наша» компиляция той же серии
        if not extras_books and not extra_comps:
            continue
        comp_xml = read_book(root, comp)
        vols = volume_titles(comp_xml)
        avg = text_chars(comp_xml) // len(vols) if comp_xml and vols else 0
        extras = []
        for b in extras_books:
            kind = classify(b, read_book(root, b), comp, vols, avg)
            kind_counts[kind] += 1
            extras.append({**_book_row(b), "kind": kind})
        for b in extra_comps:
            kind_counts["omnibus"] += 1
            extras.append({**_book_row(b), "kind": "omnibus"})
        result.append({
            "author": author_of(comp), "series": series_name[key], "genre": genre_of(comp),
            "compilation": {**_book_row(comp), "volumes": vols}, "issues": compilation_issues(comp, vols),
            "extras": sorted(extras, key=lambda e: list(KINDS).index(e["kind"])),
        })

    genre_splits = []
    for key, group in groups.items():
        counts = Counter(genre_of(b) for b in group)
        if len(counts) > 1:
            top = counts.most_common()
            genre_splits.append({
                "author": author_of(group[0]), "series": series_name[key], "genres": top,
                "majority": top[0][0] if top[0][1] > top[1][1] else None,
            })

    pseudonyms = defaultdict(lambda: {"count": 0, "folders": set(), "example": ""})
    for b in books:
        m = PSEUDONYM.match(b.title or "")
        if m and norm(m.group(1)) != norm(m.group(2)):
            entry = pseudonyms[(m.group(1).strip(), m.group(2).strip())]
            entry["count"] += 1
            entry["folders"].add(author_of(b))
            entry["example"] = entry["example"] or b.title
    pseudonym_rows = [{"alias": a, "name": n, "count": e["count"], "folders": sorted(e["folders"]),
                       "example": e["example"]} for (a, n), e in sorted(pseudonyms.items())]

    result.sort(key=lambda r: (r["author"].lower(), r["series"].lower()))
    return {
        "series": result,
        "kind_counts": dict(kind_counts),
        "genre_splits": sorted(genre_splits, key=lambda r: (r["author"].lower(), r["series"].lower())),
        "pseudonyms": pseudonym_rows,
        "totals": {"books": len(books), "series_with_compilation": len(with_comp),
                   "series_with_extras": len(result), "extra_books": sum(kind_counts.values())},
    }
