"""Регрессия для `SynchronizationService._build_folder_structure()`/
`_move_files()` — docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (замечен пользователем): баг №109/п.1 убрал
автоматическую склейку "Мир Вальдиры\\<Подсерия>" в `proposed_series`
для подсерий без собственного ведущего номера ("Герой крайних
рубежей", "Цикл Люца" и т.п.) — это правильно защищает нумерацию
позиций компилятора от коллизий между независимыми сериями (см.
`test_dominant_folder_position_collision_guard.py`), но как побочный
эффект убирало папку "Мир Вальдиры" и из ФИЗИЧЕСКОГО пути при
синхронизации в библиотеку — пользователь явно попросил сохранить эту
папку-группировку на диске, даже когда она больше не общее
пространство нумерации.

Решение: `regen_csv.py` теперь пишет отброшенный корень в отдельное
поле `record.series_display_root` (не в `proposed_series` — чтобы не
трогать нумерацию), а синхронизация оборачивает `series`/`subseries`
в него при построении целевого пути.
"""
from pathlib import Path

from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _make_service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.logger = Logger()
    svc.log_callback = None
    svc.db_path = tmp_path / "nonexistent.db"
    svc.stats = {}
    return svc


def _rec(file_path, proposed_series, series_display_root="", proposed_author="Михайлов Руслан",
         author_folder_root=""):
    return BookRecord(
        file_path=file_path, file_title=Path(file_path).stem,
        metadata_authors=proposed_author, proposed_author=proposed_author,
        author_source="folder_dataset",
        metadata_series="", proposed_series=proposed_series, series_source="folder_dataset",
        series_number="", metadata_genre="sf_fantasy",
        series_display_root=series_display_root,
        author_folder_root=author_folder_root,
    )


class TestDisplayRootWrapsSeriesInTargetPath:
    def test_display_root_nests_series_folder(self, tmp_path):
        svc = _make_service(tmp_path)
        records = [_rec("ГКР-1.fb2", "Герой крайних рубежей", "Мир Вальдиры")]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["ГКР-1.fb2"]
        assert display_root == "Мир Вальдиры"
        assert series == "Герой крайних рубежей"

    def test_no_display_root_unaffected(self, tmp_path):
        # Sanity: обычная (не сплющенная) серия без display_root не меняется.
        svc = _make_service(tmp_path)
        records = [_rec("1.fb2", "Обычная серия")]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["1.fb2"]
        assert display_root == ""
        assert series == "Обычная серия"


class TestDisplayRootInSingleVolumeFilename:
    """Реальный случай (замечен пользователем): одиночные, ещё не
    объединённые в компиляцию тома одной и той же серии называются
    БЕЗ организационного корня ("Михайлов Руслан - Господство клана
    Неспящих. Гром небесный т. 4.fb2"), в то время как УЖЕ
    скомпилированная группа той же серии его показывает ("Михайлов
    Руслан - Мир Вальдиры. Господство клана Неспящих (ч. 6-7 в 3
    книгах).fb2") — `_build_target_filename()` строит имя одиночного
    тома из `record.proposed_series` напрямую, не заглядывая в
    `record.series_display_root` (в отличие от `CompilationGroup`,
    который это поле уже подхватывает через `_group_series_for_naming`).
    """

    def test_single_volume_includes_display_root(self, tmp_path):
        svc = _make_service(tmp_path)
        rec = _rec("Господство клана Неспящих 4. Гром небесный.fb2",
                    "Господство клана Неспящих", "Мир Вальдиры")
        rec.file_title = "Гром небесный"
        rec.series_number = "4"
        kind, vols, _conf = svc._classify_record(rec)
        name = svc._build_target_filename(rec, kind, vols)
        assert name == "Михайлов Руслан - Мир Вальдиры. Господство клана Неспящих. Гром небесный т. 4.fb2"

    def test_single_volume_without_display_root_unaffected(self, tmp_path):
        svc = _make_service(tmp_path)
        rec = _rec("Обычная серия 1. Название.fb2", "Обычная серия")
        rec.file_title = "Название"
        rec.series_number = "1"
        kind, vols, _conf = svc._classify_record(rec)
        name = svc._build_target_filename(rec, kind, vols)
        assert name == "Михайлов Руслан - Обычная серия. Название т. 1.fb2"


class TestAuthorFolderRootUsedForFolderPathOnly:
    """Реальный случай (Маркова Юлия, Михайловский Александр / "В
    закоулках мироздания"): 2 из 18 файлов серии в СВОИХ метаданных
    указывают только одного автора — их `proposed_author` остаётся
    узким (файл/теги в FB2 не трогаем), но `author_folder_root`
    (стабильный консенсус всей группы, см. Pass4Consensus) должен
    определять папку АВТОРА при синхронизации — иначе эти 2 файла
    физически уезжают в другую авторскую папку библиотеки, чем
    остальные 16 той же серии.
    """

    def test_folder_uses_author_folder_root_not_proposed_author(self, tmp_path):
        svc = _make_service(tmp_path)
        records = [_rec(
            "17.fb2", "В закоулках мироздания",
            proposed_author="Михайловский Александр",
            author_folder_root="Маркова Юлия, Михайловский Александр",
        )]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["17.fb2"]
        assert author == "Маркова Юлия, Михайловский Александр"

    def test_no_author_folder_root_falls_back_to_proposed_author(self, tmp_path):
        # Sanity: без author_folder_root поведение не меняется.
        svc = _make_service(tmp_path)
        records = [_rec("1.fb2", "Обычная серия", proposed_author="Автор Тест")]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["1.fb2"]
        assert author == "Автор Тест"


class TestPrequelMarkerPreservedInTargetFilename:
    """По просьбе пользователя (баг №109, продолжение): если исходное имя
    файла несёт пометку "(рассказ-приквел)"/"(приквел)" и т.п. — при
    переименовании одиночного (ещё не скомпилированного) тома эта
    пометка должна сохраняться в итоговом имени, иначе читатель не
    поймёт, что перед ним приквел, а не пропущенный по номеру том
    основной серии. Реальный случай: "+ Пожиратель душ (рассказ-
    приквел).fb2" → раньше "Пехов Алексей - Миры Крадущегося 2. Ветер и
    искры. Пожиратель душ.fb2" (пометка потеряна).
    """

    def test_prequel_marker_kept_in_renamed_file(self, tmp_path):
        svc = _make_service(tmp_path)
        rec = _rec(
            "+ Пожиратель душ (рассказ-приквел).fb2",
            "Миры Крадущегося 2. Ветер и искры",
            proposed_author="Пехов Алексей",
        )
        rec.file_title = "Пожиратель душ"
        kind, vols, _conf = svc._classify_record(rec)
        name = svc._build_target_filename(rec, kind, vols)
        assert name == (
            "Пехов Алексей - Миры Крадущегося 2. Ветер и искры. "
            "Пожиратель душ (рассказ-приквел).fb2"
        )

    def test_bare_prequel_marker_variant_also_kept(self, tmp_path):
        svc = _make_service(tmp_path)
        rec = _rec(
            "Цена свободы (приквел).fb2",
            "Миры Крадущегося 2. Ветер и искры",
            proposed_author="Пехов Алексей",
        )
        rec.file_title = "Цена свободы"
        kind, vols, _conf = svc._classify_record(rec)
        name = svc._build_target_filename(rec, kind, vols)
        assert name.endswith("(приквел).fb2")

    def test_no_prequel_marker_unaffected(self, tmp_path):
        # Sanity: обычный том без пометки называется как раньше.
        svc = _make_service(tmp_path)
        rec = _rec(
            "Гром небесный.fb2", "Обычная серия",
            proposed_author="Автор Тест",
        )
        rec.file_title = "Гром небесный"
        kind, vols, _conf = svc._classify_record(rec)
        name = svc._build_target_filename(rec, kind, vols)
        assert name == "Автор Тест - Обычная серия. Гром небесный.fb2"
