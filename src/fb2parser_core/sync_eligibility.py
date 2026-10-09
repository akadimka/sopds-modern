"""Допуск папки к синхронизации.

Синхронизировать можно только папку, у которой КАЖДЫЙ FB2-файл (на любой
глубине вложенности) уже размечен жанрами дерева: все значения `<genre>` в
`<title-info>` — имена узлов дерева жанров (любого уровня). Папку без
единой книги синхронизировать тоже нечего.

Раньше допуск держался на временном кеше «папкам назначили жанр в этой
сессии» (3 часа, терялся при перезапуске) — после перерыва уже размеченную
папку синхронизировать было нельзя. Проверка по самим файлам от кеша не
зависит.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from .fb2_author_extractor import _TITLE_INFO_RE, _genres_from_title_info
from .fb2_utils import fb2_rglob, read_fb2_bytes

_HEAD_BYTES = 65536  # <title-info> всегда в начале файла
_ENCODING_RE = re.compile(rb'encoding\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass
class FolderCheck:
    path: str
    total: int = 0
    genres: List[str] = field(default_factory=list)  # жанры дерева в папке, по убыванию числа книг
    bad_files: List[str] = field(default_factory=list)  # пути относительно папки
    outside: bool = False  # папка вне исходной папки синхронизации

    @property
    def eligible(self) -> bool:
        return not self.outside and self.total > 0 and not self.bad_files


def _decode_head(path: Path) -> str:
    if path.name.lower().endswith(".zip"):
        raw = read_fb2_bytes(path)[:_HEAD_BYTES]
    else:
        with open(path, "rb") as f:
            raw = f.read(_HEAD_BYTES)
    m = _ENCODING_RE.search(raw[:256])
    declared = m.group(1).decode("ascii", "replace") if m else ""
    for enc in ("utf-8", declared, "cp1251"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def file_genres(path: Path) -> List[str]:
    """Значения `<genre>` из `<title-info>` файла (пустой список — нет жанра
    или файл не читается)."""
    try:
        m = _TITLE_INFO_RE.search(_decode_head(path))
    except OSError:
        return []
    return _genres_from_title_info(m.group(0)) if m else []


def check_folder(folder: str, tree_names: Iterable[str]) -> FolderCheck:
    """Проверить папку по правилу допуска (см. docstring модуля)."""
    names = set(tree_names)
    root = Path(folder)
    result = FolderCheck(path=folder)
    if not root.is_dir():
        return result
    counts: dict = {}
    for fb2 in fb2_rglob(root):
        result.total += 1
        genres = file_genres(fb2)
        if not genres or any(g not in names for g in genres):
            result.bad_files.append(str(fb2.relative_to(root)))
            continue
        for g in genres:
            counts[g] = counts.get(g, 0) + 1
    result.genres = sorted(counts, key=lambda g: (-counts[g], g))
    return result
