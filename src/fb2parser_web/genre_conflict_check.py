"""Проверка конфликта жанров при массовом присвоении жанра папке.

GenreAssignmentService (fb2parser_core/genre_assign.py) переписывает <genre>
у КАЖДОГО файла в папке на одно и то же значение — без оглядки на то, что
отдельная книга внутри сборника может быть чужеродной (например, автор
детективов, чья книга случайно попала в сборник фантастики). Эта проверка
не блокирует присвоение — она сверяется с уже накопленной историей автора
в каталоге SOPDS (opds_catalog) и возвращает список файлов, для которых
новый жанр расходится со всей прежней историей автора, чтобы решение
принимал пользователь, а не эвристика.
"""
from pathlib import Path
from typing import Dict, List

from fb2parser_core.fb2_author_extractor import FB2AuthorExtractor
from fb2parser_core.fb2_utils import fb2_rglob

# Ниже этого числа прежних книг автора в каталоге история слишком мала,
# чтобы по ней делать выводы — только новые/малоизвестные авторы, для
# которых конфликт был бы просто шумом (см. cold-start в обсуждении).
_MIN_HISTORY_BOOKS = 3


def check_genre_conflicts(folder_path: str, target_genre: str, config_path: str) -> List[Dict]:
    """Найти файлы в папке, чей автор в каталоге SOPDS устойчиво числится
    в жанре, отличном от ``target_genre``.

    Returns:
        Список словарей ``{"file": относительный_путь, "author": имя,
        "author_genres": {жанр: число_книг}}`` — по одной записи на
        файл+автора с конфликтом. Пустой список — конфликтов не найдено
        (в т.ч. если автор не сопоставлен с каталогом или его история мала).
    """
    from django.db.models import Count
    from opds_catalog.models import Author, bgenre

    folder = Path(folder_path)
    fb2_files = fb2_rglob(folder)
    if not fb2_files:
        return []

    extractor = FB2AuthorExtractor(config_path)
    conflicts: List[Dict] = []

    # Кэш по автору в пределах одного вызова — сборник обычно содержит
    # много книг одного и того же автора, не бьём в БД за каждую заново.
    genre_history_cache: Dict[str, Dict[str, int]] = {}

    for fb2_file in fb2_files:
        try:
            meta = extractor._extract_all_metadata_at_once(fb2_file)
        except Exception:
            continue
        authors_str = (meta.get('authors') or '').strip()
        if not authors_str:
            continue

        for author_name in [a.strip() for a in authors_str.split(';') if a.strip()]:
            key = author_name.upper()
            if key not in genre_history_cache:
                # FB2 <author> хранит first-name/last-name в порядке "Имя Фамилия"
                # (см. fb2_author_extractor._extract_all_metadata_at_once), а
                # каталог SOPDS — в порядке "Фамилия Имя" (opdsdb.py). Ищем
                # обоими порядками слов, не полагаясь на словарь имён.
                words = author_name.split()
                candidates = {key}
                if len(words) == 2:
                    candidates.add(f"{words[1]} {words[0]}".upper())
                author = Author.objects.filter(search_full_name__in=candidates).first()
                if not author:
                    genre_history_cache[key] = {}
                    continue
                counts = (
                    bgenre.objects
                    .filter(book__bauthor__author_id=author.id)
                    .values("genre__subsection")
                    .annotate(cnt=Count("book_id", distinct=True))
                )
                genre_history_cache[key] = {c["genre__subsection"]: c["cnt"] for c in counts}

            genre_counts = genre_history_cache[key]
            total_books = sum(genre_counts.values())
            if total_books < _MIN_HISTORY_BOOKS:
                continue  # автор не сопоставлен или история слишком мала
            if target_genre in genre_counts:
                continue  # автор уже отмечен в этом жанре — не конфликт

            try:
                rel = str(fb2_file.relative_to(folder))
            except ValueError:
                rel = str(fb2_file)
            conflicts.append({
                "file": rel,
                "author": author_name,
                "author_genres": genre_counts,
            })

    return conflicts
