"""
Мост к fb2parser_core — тонкий слой для функций, требующих config.json.
Все пользовательские данные (config, app_settings, genres.xml) хранятся
в src/fb2_data/ внутри проекта sopds-modern.
"""
import os

_FB2_DATA_DIR      = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "fb2_data"))
_FB2_SETTINGS_DIR  = os.path.join(_FB2_DATA_DIR, "settings")
_FB2_CSV_DIR       = os.path.join(_FB2_DATA_DIR, "csv")


def _config_path() -> str:
    return os.path.join(_FB2_SETTINGS_DIR, "config.json")


def _genres_path() -> str:
    return os.path.join(_FB2_DATA_DIR, "genres.xml")


def _csv_dir() -> str:
    os.makedirs(_FB2_CSV_DIR, exist_ok=True)
    return _FB2_CSV_DIR


def get_genres_manager():
    from fb2parser_core.genres_manager import GenresManager
    gm = GenresManager(_genres_path())
    gm.load()
    return gm


def _memory_path() -> str:
    return os.path.join(_FB2_SETTINGS_DIR, "library_memory.db")


def get_library_memory():
    from fb2parser_core.library_memory import LibraryMemory
    return LibraryMemory(_memory_path())


def _make_codes_recorder():
    """Запись исходных кодов <genre> в память автосинхронизации перед тем,
    как ручное назначение жанра их перепишет (docs/watch-folder-autosync-design.md).

    Порция — папка, которой назначают жанр; при назначении отдельным файлам
    — папка сканирования целиком (осторожно: несколько её подпапок считаются
    ОДНОЙ порцией, поэтому правило «код → жанр» из таких назначений
    становится надёжным не быстрее, чем на самом деле подтверждено).
    """
    from fb2parser_core.settings_manager import SettingsManager
    memory = get_library_memory()
    scan_root = SettingsManager(_config_path()).get_last_scan_path() or ""

    def record(fb2_path, content, batch):
        memory.record_from_text(content, batch or scan_root or os.path.dirname(str(fb2_path)), "user")

    return record


def get_genre_assignment_service(logger=None):
    from fb2parser_core.genre_assign import GenreAssignmentService
    return GenreAssignmentService(logger=logger, codes_recorder=_make_codes_recorder())


def get_autosync_service():
    from fb2parser_core.autosync_service import AutosyncService
    return AutosyncService(_config_path(), get_library_memory(), get_genres_manager())


def get_sync_service():
    from fb2parser_core.synchronization import SynchronizationService
    return SynchronizationService(_config_path())


def get_normalization_settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())
