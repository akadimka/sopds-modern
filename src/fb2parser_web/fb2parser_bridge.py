"""
Мост к fb2parser_core — тонкий слой для функций, требующих config.json.
config.json и app_settings.json берутся из пути FB2PARSER_PATH (constance),
так как там хранятся пользовательские настройки (жанры, словари имён и т.д.)
"""
import os


def _config_path() -> str:
    from constance import config as cfg
    return os.path.join(cfg.FB2PARSER_PATH, "config.json")


def get_genres_manager():
    from fb2parser_core.genres_manager import GenresManager
    from constance import config as cfg
    genres_xml = os.path.join(cfg.FB2PARSER_PATH, "genres.xml")
    gm = GenresManager(genres_xml)
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
