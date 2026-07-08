"""
Мост к fb2parser проекту: добавляет путь в sys.path и предоставляет
безопасный импорт ключевых сервисов.
"""
import importlib
import sys
import os


def _get_fb2parser_path() -> str:
    from constance import config
    return config.FB2PARSER_PATH


def _ensure_path():
    """Добавить fb2parser в sys.path если ещё не добавлен."""
    path = _get_fb2parser_path()
    if path and path not in sys.path:
        sys.path.insert(0, path)
    return path


def get_genres_manager():
    """Вернуть GenresManager, загруженный из genres.xml в fb2parser dir."""
    path = _ensure_path()
    gm_mod = importlib.import_module("genres_manager")
    gm = gm_mod.GenresManager(os.path.join(path, "genres.xml"))
    gm.load()
    return gm


def get_genre_assignment_service(logger=None):
    """Вернуть GenreAssignmentService."""
    _ensure_path()
    mod = importlib.import_module("genre_assign")
    return mod.GenreAssignmentService(logger=logger)


def assign_genre_threaded(folder_path, genre_name, progress_callback=None,
                          completion_callback=None, logger=None):
    """Запустить присвоение жанра в фоновом потоке."""
    _ensure_path()
    mod = importlib.import_module("genre_assign")
    return mod.assign_genre_threaded(
        folder_path, genre_name,
        progress_callback=progress_callback,
        completion_callback=completion_callback,
        logger=logger,
    )


def get_sync_service():
    """Вернуть SynchronizationService, настроенный через config.json в fb2parser dir."""
    path = _ensure_path()
    mod = importlib.import_module("synchronization")
    config_path = os.path.join(path, "config.json")
    return mod.SynchronizationService(config_path)


def get_normalization_settings():
    """Вернуть объект настроек из fb2parser (SettingsManager)."""
    path = _ensure_path()
    mod = importlib.import_module("settings_manager")
    config_path = os.path.join(path, "config.json")
    return mod.SettingsManager(config_path)
