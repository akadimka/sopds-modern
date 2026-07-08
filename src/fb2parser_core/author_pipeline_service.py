"""Облегчённый пайплайн только для извлечения авторов (без серий/жанров)."""
from pathlib import Path
from typing import List

try:
    from precache import Precache
    from passes.pass1_read_files import Pass1ReadFiles
    from passes.pass2_filename import Pass2Filename
    from passes.pass2_fallback import Pass2Fallback
    from fb2_author_extractor import FB2AuthorExtractor
except ImportError:
    from .precache import Precache
    from .passes.pass1_read_files import Pass1ReadFiles
    from .passes.pass2_filename import Pass2Filename
    from .passes.pass2_fallback import Pass2Fallback
    from .fb2_author_extractor import FB2AuthorExtractor


def run_author_only_pipeline(
    folder_path,
    settings,
    logger,
    folder_parse_limit=None,
) -> list:
    """Запустить Precache + Pass1 + Pass2 + Pass2Fallback для извлечения авторов.

    Не запускает серийные/жанровые пассы — быстрее чем полный CSV-пайплайн.
    Используется когда нужны только proposed_author значения.

    Returns:
        Список BookRecord с заполненными proposed_author.
    """
    work_dir = Path(folder_path)
    extractor = FB2AuthorExtractor()

    precache = Precache(work_dir, settings, logger, folder_parse_limit)
    precache.execute()

    pass1 = Pass1ReadFiles(
        work_dir, precache.author_folder_cache, extractor, logger, folder_parse_limit
    )
    records = pass1.execute()

    pass2 = Pass2Filename(
        settings, logger, work_dir,
        male_names=precache.male_names,
        female_names=precache.female_names,
    )
    pass2.prebuild_author_cache(records)
    pass2.execute(records)

    Pass2Fallback(logger, settings=settings).execute(records)

    return records


def guess_first_name(author: str, author_source: str) -> str:
    """Угадать имя автора по формату источника.

    Источник 'filename' хранит автора в западном порядке «Имя Фамилия»,
    все остальные — в русском «Фамилия Имя».
    """
    parts = author.split()
    if not parts:
        return ""
    if author_source == "filename":
        return parts[0] if len(parts) >= 2 else ""  # западный порядок: первое слово = имя
    return parts[1] if len(parts) >= 2 else ""      # русский порядок: второе слово = имя


def collect_unknown_gender_authors(records: list, male_set: set, female_set: set) -> list:
    """Собрать авторов с неопределённым полом из списка записей.

    Returns:
        Список кортежей (source, author, first_name, gender, file_path)
        где gender пустой (пол не определён).
    """
    import re as _re
    rows = []
    seen: set = set()
    for rec in records:
        combined = rec.proposed_author or ""
        if not combined or combined == "Сборник":
            continue
        source = rec.author_source or ""
        authors = [a.strip() for a in _re.split(r'[,;]+', combined) if a.strip()]
        for author in authors:
            if author in seen:
                continue
            seen.add(author)
            parts = author.split()
            first_name = guess_first_name(author, source)
            gender = ""
            for word in parts:
                w = word.lower()
                if w in male_set:
                    gender = "Муж."
                    break
                if w in female_set:
                    gender = "Жен."
                    break
            if gender:
                continue  # уже известен — пропускаем
            rows.append((source, author, first_name, gender, rec.file_path or ""))
    return rows
