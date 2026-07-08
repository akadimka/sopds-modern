"""
Утилиты для работы с FB2 и FB2.ZIP файлами.

fb2.zip — это обычный ZIP-архив, содержащий один .fb2 файл внутри.
Все функции прозрачно работают с обоими форматами.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterator, List


def is_fb2_zip(path: Path) -> bool:
    """True если файл — ZIP-архив (имеет сигнатуру PK)."""
    try:
        raw = path.read_bytes(4) if hasattr(path, 'read_bytes') else b''
        return raw[:2] == b'PK'
    except Exception:
        return False


def fb2_stem(path: Path) -> str:
    """Логический stem FB2-файла без расширений.

    "Автор. Книга 1.fb2"     → "Автор. Книга 1"
    "Автор. Книга 1.fb2.zip" → "Автор. Книга 1"
    """
    name = path.name
    if name.lower().endswith('.fb2.zip'):
        return name[:-8]
    return path.stem


def fb2_rglob(directory: Path) -> List[Path]:
    """Рекурсивно найти все FB2 и FB2.ZIP файлы в каталоге."""
    plain = list(directory.rglob('*.fb2'))
    zipped = list(directory.rglob('*.fb2.zip'))
    return sorted(plain + zipped, key=lambda p: str(p).lower())


def fb2_count(directory: Path) -> int:
    """Количество FB2/FB2.ZIP файлов в каталоге."""
    return sum(1 for _ in directory.rglob('*.fb2')) + \
           sum(1 for _ in directory.rglob('*.fb2.zip'))


def read_fb2_bytes(path: Path) -> bytes:
    """Прочитать содержимое FB2 (XML) из файла или zip-архива."""
    raw = path.read_bytes()
    if raw[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = zf.namelist()
                fb2_name = next(
                    (n for n in names if n.lower().endswith('.fb2')),
                    names[0] if names else None,
                )
                if fb2_name:
                    return zf.read(fb2_name)
        except Exception:
            pass
    return raw


def write_fb2_bytes(path: Path, xml_bytes: bytes) -> None:
    """Записать XML обратно в файл с сохранением формата (zip или plain).

    Если файл был zip — пересохраняет zip с тем же именем внутри архива.
    """
    if path.name.lower().endswith('.fb2.zip'):
        inner_name = path.name[:-4]  # strip .zip → "book.fb2"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(inner_name, xml_bytes)
        path.write_bytes(buf.getvalue())
    else:
        # Для обычного .fb2 — просто запись
        path.write_bytes(xml_bytes)


def has_fb2_files(directory: Path) -> bool:
    """True если в директории есть хотя бы один FB2/FB2.ZIP файл."""
    for _ in directory.rglob('*.fb2'):
        return True
    for _ in directory.rglob('*.fb2.zip'):
        return True
    return False
