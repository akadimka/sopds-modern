"""
Модуль для консенсуса автора/серии по группе файлов (Pass4Consensus).

Баг №105: раньше модуль также реализовывал прямое regex-извлечение серии
из filename/filepath (`extract_series_from_filename`/
`extract_series_from_filepath`/`extract_sequence_number`/
`extract_series_combined`/`categorize_series`) — ни один внешний
вызывающий к этим методам не обращался (извлечение серии по факту
выполняет `passes/pass2_series_filename.py`), и их код был несовместим с
реальной сигнатурой `ExtractionResult.__init__()` (звал именованные
аргументы `extracted_value=`/`source_priority=`, которых там нет) — упал
бы при первом вызове. Убрано целиком (см. docs/quality-roadmap.md, баг
№105). Осталось только `apply_author_consensus()`/
`apply_series_consensus()` (используется `passes/pass4_consensus.py`).
"""

from typing import List
from collections import defaultdict

try:
    from settings_manager import SettingsManager
    from series_normalizer import SeriesNormalizer
    from series_helpers import _nfc_lower_yo
except ImportError:
    from .settings_manager import SettingsManager
    from .series_normalizer import SeriesNormalizer
    from .series_helpers import _nfc_lower_yo


class SeriesProcessor:
    """Консенсус автора/серии по группе файлов одной папки/автора."""

    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация процессора серий.

        Args:
            config_path: Путь к файлу конфигурации
        """
        self.settings = SettingsManager(config_path)
        self.normalizer = SeriesNormalizer()

    def apply_author_consensus(self, records: List) -> int:
        """
        Применить консенсус авторов в папке.

        Args:
            records: Список BookRecord объектов

        Returns:
            Количество примененных изменений
        """

        # Группировать по папке
        groups = defaultdict(list)
        for record in records:
            from pathlib import Path
            folder = Path(record.file_path).parent
            groups[folder].append(record)

        consensus_count = 0

        for folder, group_records in groups.items():
            # Источники с высоким приоритетом (filename важнее metadata)
            _HIGH_PRIORITY = {'folder_dataset', 'folder_hierarchy', 'filename', 'filename+meta_expanded'}
            _LOW_PRIORITY  = {'metadata', 'consensus', ''}

            high_priority = [r for r in group_records if r.author_source in _HIGH_PRIORITY]
            all_sourced   = [r for r in group_records if r.author_source]

            if not all_sourced:
                continue

            # Если есть хотя бы один файл с filename-источником — он авторитетен для всей папки.
            # Используем только высокоприоритетные файлы для формирования консенсуса.
            consensus_pool = high_priority if high_priority else all_sourced

            author_counts = {}
            for record in consensus_pool:
                if record.proposed_author and record.proposed_author != 'Сборник':
                    author_counts[record.proposed_author] = author_counts.get(record.proposed_author, 0) + 1

            if not author_counts:
                continue

            consensus_author = max(author_counts, key=author_counts.get)

            # Требуем строгое большинство: консенсус-автор должен занимать ≥50% high-priority записей.
            # Это блокирует сборники, где десятки авторов и нет доминирующего.
            # Дополнительно требуем ≥2 high-priority голосов: при ОДНОМ high-priority
            # файле "большинство" (100% от 1) проходит тривиально, из-за чего один
            # случайно правильно определённый автор навязывался всей папке-антологии
            # с десятками разных настоящих авторов (баг: 45-файловая коллекция
            # "Скандинавская линия «НордБук»" с 33 разными metadata-авторами вся
            # получила автора единственного high-priority файла).
            total_hp = len(high_priority) if high_priority else len(all_sourced)
            if total_hp > 0:
                consensus_share = author_counts[consensus_author] / total_hp
                if consensus_share < 0.5 or (high_priority and len(high_priority) < 2):
                    continue  # нет большинства, либо слишком мало голосов для доверия

            # Применить ко всем файлам с низкоприоритетным или пустым источником
            for record in group_records:
                if record.author_source in _HIGH_PRIORITY:
                    continue  # не трогаем авторитетные источники
                if record.proposed_author in ('Сборник', 'Соавторство'):
                    continue  # явный коллективный маркер — не перезаписываем консенсусом
                if record.proposed_author == consensus_author:
                    continue  # уже правильно
                if record.proposed_author and record.proposed_author != 'Сборник' and not high_priority:
                    continue  # без filename-донора не перезаписываем metadata
                # Защита: полное имя из metadata (≥2 слова) не перезаписываем консенсусом папки.
                # В сборнике у каждой книги свой автор — FB2-метаданные авторитетны.
                if (record.author_source == 'metadata'
                        and record.proposed_author
                        and len(record.proposed_author.split()) >= 2):
                    continue
                record.proposed_author = consensus_author
                record.author_source = 'consensus'
                consensus_count += 1

        return consensus_count

    def apply_series_consensus(self, records: List) -> int:
        """
        Применить консенсус серий.

        Args:
            records: Список BookRecord объектов

        Returns:
            Количество примененных изменений
        """
        # Группировать по автору
        author_groups = defaultdict(list)
        for record in records:
            author = record.proposed_author or '[unknown]'
            author_groups[author].append(record)

        series_consensus_count = 0

        for author, author_records in author_groups.items():
            # Карта серия → записи
            series_base_map = {}

            for record in author_records:
                if record.extracted_series_candidate:
                    candidate = record.extracted_series_candidate
                    # extracted_series_candidate — "серия из имени файла" (см. docstring
                    # поля в pass1_read_files.py), путей папок в ней быть не должно. Но
                    # некоторые пути извлечения (pass2_series_filename.py) сохраняют сюда
                    # СЫРОЙ кандидат ДО очистки от имени автора-папки, из-за чего
                    # backslash-артефакт («Данильченко-Олег\Имперский вояж» вместо чистого
                    # «Имперский вояж») потом навязывался консенсусом другим файлам того
                    # же автора в СОВСЕМ других папках. Берём только хвост после '\\'.
                    if '\\' in candidate:
                        candidate = candidate.rsplit('\\', 1)[-1].strip()
                    normalized = self.normalizer.normalize_series_for_consensus(candidate)
                    if normalized not in series_base_map:
                        series_base_map[normalized] = []
                    series_base_map[normalized].append(record)

            # Для каждой базы серий
            for series_base, source_records in series_base_map.items():
                for target_record in author_records:
                    if target_record.proposed_series and target_record.series_source not in ('metadata', 'metadata_folder_confirmed'):
                        continue

                    if target_record.extracted_series_candidate:
                        continue

                    # Проверить, содержит ли имя файла базу серии
                    from pathlib import Path
                    filename = Path(target_record.file_path).stem
                    filename_normalized = _nfc_lower_yo(filename)
                    series_base_normalized = _nfc_lower_yo(series_base)

                    if len(series_base_normalized) < 2:
                        continue

                    if series_base_normalized in filename_normalized:
                        # Проверить позицию
                        dash_pos = filename_normalized.find(' - ')
                        dot_pos = filename_normalized.find('. ')
                        base_pos = filename_normalized.find(series_base_normalized)

                        applies = False
                        if dash_pos >= 0 and base_pos > dash_pos:
                            applies = True
                        elif dot_pos >= 0 and base_pos > dot_pos:
                            applies = True
                        elif base_pos == 0:
                            applies = True

                        if applies:
                            if target_record.series_source == 'no_series_folder':
                                continue
                            target_record.proposed_series = series_base
                            target_record.series_source = 'author-consensus'

                            # Проверить подтверждение метаданными
                            if (target_record.metadata_series and
                                self.normalizer.normalize_series_for_consensus(target_record.metadata_series) == series_base):
                                target_record.series_source = 'author-consensus (metadata-confirmed)'

                            series_consensus_count += 1

        return series_consensus_count

