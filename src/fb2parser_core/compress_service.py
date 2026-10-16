"""Сервис сжатия плоских .fb2 файлов библиотеки в .fb2.zip.

Только для library_path — не трогает last_scan_path/исходники. Не трогает
уже сжатые .fb2.zip и multi-book архивы (CAT_ZIP/CAT_INP) — только голые
.fb2 файлы, найденные рекурсивно.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .fb2_utils import compress_fb2_file


def compress_library(
    library_path: Path,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    on_compressed: Optional[Callable[[Path, Path], None]] = None,
) -> dict:
    """Сжать все плоские .fb2 файлы в library_path.

    on_compressed(было, стало) — после каждого сжатого файла: обновить
    каталоги, которые помнят путь книги (OPDS, БД библиотеки fb2parser).

    Returns:
        {"compressed": int, "errors": list[str], "bytes_saved": int,
         "processed": int, "total": int, "stopped": bool}
    """
    files = sorted(p for p in library_path.rglob('*.fb2') if p.is_file())
    total = len(files)
    compressed = 0
    bytes_saved = 0
    errors: list = []
    stopped = False

    for i, path in enumerate(files):
        if stop_check and stop_check():
            stopped = True
            break
        if progress_callback:
            progress_callback(i, total, str(path.relative_to(library_path)))
        try:
            bytes_saved += compress_fb2_file(path)
            compressed += 1
        except Exception as e:
            errors.append(f"{path.relative_to(library_path)}: {e}")
            continue
        if on_compressed:
            try:
                on_compressed(path, path.with_name(path.name + '.zip'))
            except Exception as e:
                errors.append(f"{path.relative_to(library_path)}: каталог не обновлён: {e}")

    return {
        "compressed": compressed,
        "errors": errors,
        "bytes_saved": bytes_saved,
        "processed": min(i + 1, total) if total else 0,
        "total": total,
        "stopped": stopped,
    }
