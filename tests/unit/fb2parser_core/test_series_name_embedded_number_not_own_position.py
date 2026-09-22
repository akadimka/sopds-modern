"""Регрессия для `FB2CompilerService._determine_sort_key()` — docs/quality-
roadmap.md, баг №109 (продолжение).

Реальный случай (Пехов Алексей / "Миры Крадущегося 2. Ветер и искры"):
приквелы-рассказы "+ Пожиратель душ (рассказ-приквел).fb2" и "+ Цена
свободы (рассказ-приквел).fb2" не имеют собственного `series_number`
(нет ни в метаданных, ни в имени файла) — по решению пользователя такие
файлы просто остаются отдельными, не сливаются с основной серией.

Но при синхронизации (кто ещё не скомпилирован) `_build_target_filename()`
переименовывает их в "Автор - <Серия>. <Заголовок>.fb2" — здесь
proposed_series САМ содержит число как часть имени ("Миры Крадущегося
**2**. Ветер и искры" — "2" это номер МИРА, часть имени серии, не
позиция книги). При повторном скане (auto_compile_library на уже
переименованных файлах библиотеки) "Источник Б" (`_determine_sort_key`)
слепо находит "2." где угодно в stem и принимает его за позицию книги
— ОБА приквела получают ОДИНАКОВЫЙ ложный sort_key=(0,2,0,0), что и
запускает дедупликацию/удаление как "дубликатов" уже готового омнибуса
"Ветер и искры. Тетралогия.fb2" — реальная потеря данных.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_SERIES = "Миры Крадущегося 2. Ветер и искры"


def _rec(path, title):
    return BookRecord(
        file_path=path, file_title=title, metadata_authors="Алексей Пехов",
        proposed_author="Пехов Алексей", author_source="folder_dataset",
        metadata_series="", proposed_series=_SERIES,
        series_source="folder_dataset", series_number="",
    )


class TestSeriesNameOwnNumberNotMistakenForBookPosition:
    def test_prequel_renamed_with_series_prefix_stays_unpositioned(self):
        rec = _rec(
            f"Пехов Алексей - {_SERIES}. Пожиратель душ.fb2",
            "Пожиратель душ",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key[0] != 0 or sort_key[1] != 2

    def test_prequels_and_existing_omnibus_survive_find_groups_without_loss(self, tmp_path):
        # End-to-end: воспроизводит реальный случай — 2 приквела без своей
        # позиции + уже готовый омнибус "Тетралогия" в одной папке серии,
        # ПОСЛЕ переименования синхронизацией (имя серии внутри имени
        # файла). Раньше оба приквела получали одинаковую ложную позицию
        # "2" (номер мира из имени серии) и терялись как "дубликаты"
        # омнибуса — реальная потеря данных при auto-compile.
        def _rec2(path, title):
            return BookRecord(
                file_path=path, file_title=title, metadata_authors="Алексей Пехов",
                proposed_author="Пехов Алексей", author_source="folder_dataset",
                metadata_series="", proposed_series=_SERIES,
                series_source="folder_dataset", series_number="",
            )

        records = [
            _rec2(f"Пехов Алексей - {_SERIES}. Пожиратель душ.fb2", "Пожиратель душ"),
            _rec2(f"Пехов Алексей - {_SERIES}. Цена свободы.fb2", "Цена свободы"),
            _rec2("Ветер и искры. Тетралогия.fb2", "Ветер и искры"),
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        deleted = {p.name for g in groups for p in (g.duplicate_paths or [])}
        assert "Пехов Алексей - " + _SERIES + ". Пожиратель душ.fb2" not in deleted
        assert "Пехов Алексей - " + _SERIES + ". Цена свободы.fb2" not in deleted

    def test_genuine_own_number_after_series_name_still_recognised(self):
        # Sanity: настоящий номер тома ПОСЛЕ имени серии (не совпадающий с
        # числом внутри самого имени серии) по-прежнему распознаётся —
        # фикс не должен маскировать реальные позиции целиком.
        rec = _rec(f"Пехов Алексей - {_SERIES}. 7. Название.fb2", "Название")
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key[0] == 0 and sort_key[1] == 7
