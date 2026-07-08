"""
Мост к fb2parser_core — тонкий слой для функций, требующих config.json.
Все пользовательские данные (config, app_settings, genres.xml) хранятся
в src/fb2_data/ внутри проекта sopds-modern.
"""
import os

_FB2_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "fb2_data")


def _config_path() -> str:
    return os.path.normpath(os.path.join(_FB2_DATA_DIR, "config.json"))


def _genres_path() -> str:
    return os.path.normpath(os.path.join(_FB2_DATA_DIR, "genres.xml"))


def get_genres_manager():
    from fb2parser_core.genres_manager import GenresManager
    gm = GenresManager(_genres_path())
    gm.load()
    return gm


def get_genre_assignment_service(logger=None):
    from fb2parser_core.genre_assign import GenreAssignmentService
    return GenreAssignmentService(logger=logger)


def assign_genre_threaded(folder_path, genre_name, progress_callback=None,
                          completion_callback=None, logger=None):
    from fb2parser_core.genre_assign import assign_genre_threaded as _agt
    return _agt(
        folder_path, genre_name,
        progress_callback=progress_callback,
        completion_callback=completion_callback,
        logger=logger,
    )


def get_sync_service():
    from fb2parser_core.synchronization import SynchronizationService
    return SynchronizationService(_config_path())


def get_normalization_settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())
