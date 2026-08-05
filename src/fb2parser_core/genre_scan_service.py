"""Сервис сканирования FB2-файлов для группировки по набору жанров.

Портировано из десктопного варианта (scan_service.py) — в отличие от
основного SOPDS-сканера (opds_catalog.sopdscan.opdsScanner), который
каталогизирует книги в БД и группирует их по ОДНОМУ нормализованному жанру
из таксономии Genre, этот инструмент читает FB2-файлы в произвольной папке
напрямую и группирует по ТОЧНОЙ строке из <genre>-тегов (как есть, без
классификации) — так виден полный набор жанров каждого файла и файлы
вообще без жанра, что важно при подготовке жанровой разметки.
"""
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .fb2_utils import fb2_rglob
from .fb2_author_extractor import FB2AuthorExtractor


def scan_fb2_genres(
    folder_path: Path,
    config_path: str,
    on_progress: Optional[Callable[[int, int, int], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Извлечь жанры из всех FB2-файлов в папке.

    Args:
        folder_path: путь к папке с FB2-файлами.
        config_path: путь к config.json для FB2AuthorExtractor.
        on_progress: callback(done, total, pct) — каждые 20 файлов.
        stop_check: callback() -> bool — если возвращает True, сканирование
            прерывается досрочно (кооперативная отмена).

    Returns:
        dict с ключами:
            results   — {genre_combo: [relative_path, ...]}
            errors    — [error_str, ...]
            total     — общее число файлов
            processed — сколько файлов реально обработано (< total при отмене)
            stopped   — True если прервано через stop_check
    """
    extractor = FB2AuthorExtractor(config_path)
    results: Dict[str, List[str]] = {}
    errors: List[str] = []
    fb2_files = fb2_rglob(folder_path)
    total = len(fb2_files)
    processed = 0
    stopped = False

    for idx, fb2_file in enumerate(fb2_files, 1):
        if stop_check and stop_check():
            stopped = True
            break

        try:
            genre_str = extractor._extract_genres_from_fb2(fb2_file)
            key = genre_str.strip() if genre_str and genre_str.strip() else 'Не определено'
            try:
                rel_path = str(fb2_file.relative_to(folder_path))
            except ValueError:
                rel_path = str(fb2_file)
            results.setdefault(key, []).append(rel_path)
        except Exception as e:
            errors.append(f'{fb2_file.name}: {e}')

        processed = idx
        if on_progress and (idx % 20 == 0 or idx == total):
            on_progress(idx, total, int(idx * 100 / total) if total else 100)

    return {
        'results': results,
        'errors': errors,
        'total': total,
        'processed': processed,
        'stopped': stopped,
    }
