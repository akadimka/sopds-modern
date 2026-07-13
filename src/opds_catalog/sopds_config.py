"""Прокси-класс для настроек SOPDS из config.json.

Замена django-constance: атрибуты называются SOPDS_* для обратной совместимости,
чтобы существующий код менял только строку импорта.
"""
from __future__ import annotations

import os

# Путь к config.json относительно этого файла:
# src/opds_catalog/sopds_config.py -> src/fb2_data/settings/config.json
_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "fb2_data", "settings", "config.json")
)

_DEFAULTS: dict = {
    'root_lib': '',
    'auth': True,
    'maxitems': 10,
    'alphabet_menu': True,
    'splititems': 100,
    'doubles_hide': True,
    'book_extensions': '.pdf .djvu .fb2 .epub .mobi',
    'title_as_filename': False,
    'fb2toepub': '',
    'fb2tomobi': '',
    'fb2toazw3': '',
    'temp_dir': '',
    'fb2sax': False,
    'zipscan': True,
    'inpx_enable': False,
    'inpx_skip_unchanged': True,
    'inpx_test_zip': False,
    'inpx_test_files': False,
    'delete_logical': True,
    'scanner_pid': 'sopds_scanner.pid',
    'scanner_log': 'sopds_scanner.log',
    'scan_shed_day': 0,
    'scan_shed_dow': -1,
    'scan_shed_hour': 0,
    'scan_shed_min': 0,
    'scan_start_directly': False,
    'language': 'en-US',
}

# Маппинг SOPDS_KEY -> json_key (None = мёртвая константа, возвращаем 0)
_KEY_MAP: dict[str, str | None] = {
    'SOPDS_AUTH': 'auth',
    'SOPDS_MAXITEMS': 'maxitems',
    'SOPDS_ALPHABET_MENU': 'alphabet_menu',
    'SOPDS_SPLITITEMS': 'splititems',
    'SOPDS_DOUBLES_HIDE': 'doubles_hide',
    'SOPDS_BOOK_EXTENSIONS': 'book_extensions',
    'SOPDS_CACHE_TIME': None,   # мёртвая константа
    'SOPDS_TITLE_AS_FILENAME': 'title_as_filename',
    'SOPDS_FB2TOEPUB': 'fb2toepub',
    'SOPDS_FB2TOMOBI': 'fb2tomobi',
    'SOPDS_FB2TOAZW3': 'fb2toazw3',
    'SOPDS_TEMP_DIR': 'temp_dir',
    'SOPDS_FB2SAX': 'fb2sax',
    'SOPDS_ZIPSCAN': 'zipscan',
    'SOPDS_INPX_ENABLE': 'inpx_enable',
    'SOPDS_INPX_SKIP_UNCHANGED': 'inpx_skip_unchanged',
    'SOPDS_INPX_TEST_ZIP': 'inpx_test_zip',
    'SOPDS_INPX_TEST_FILES': 'inpx_test_files',
    'SOPDS_DELETE_LOGICAL': 'delete_logical',
    'SOPDS_SCANNER_PID': 'scanner_pid',
    'SOPDS_SCANNER_LOG': 'scanner_log',
    'SOPDS_SCAN_SHED_DAY': 'scan_shed_day',
    'SOPDS_SCAN_SHED_DOW': 'scan_shed_dow',
    'SOPDS_SCAN_SHED_HOUR': 'scan_shed_hour',
    'SOPDS_SCAN_SHED_MIN': 'scan_shed_min',
    'SOPDS_SCAN_START_DIRECTLY': 'scan_start_directly',
    'SOPDS_LANGUAGE': 'language',
}


def _get_sm():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_CONFIG_PATH)


class SopdsConfig:
    """Прокси для настроек SOPDS из config.json.

    Атрибуты называются SOPDS_* — обратная совместимость с кодом,
    ранее использовавшим django-constance.
    """

    def __getattr__(self, name: str):
        if name == 'SOPDS_ROOT_LIB':
            sm = _get_sm()
            return sm.settings.get('sopds', {}).get('root_lib', '')

        # Мёртвая константа
        if name in _KEY_MAP and _KEY_MAP[name] is None:
            return 0

        json_key = _KEY_MAP.get(name)
        if json_key is not None:
            sm = _get_sm()
            sopds = sm.settings.get('sopds', {})
            val = sopds.get(json_key, _DEFAULTS.get(json_key))
            if not val and json_key in _DEFAULTS:
                return _DEFAULTS[json_key]
            return val

        raise AttributeError(f"SopdsConfig has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        if name == 'SOPDS_ROOT_LIB':
            sm = _get_sm()
            sm.settings.setdefault('sopds', {})['root_lib'] = value
            sm.save()
            return

        json_key = _KEY_MAP.get(name)
        if json_key is not None:
            sm = _get_sm()
            if 'sopds' not in sm.settings:
                sm.settings['sopds'] = {}
            sm.settings['sopds'][json_key] = value
            sm.save()
            return

        super().__setattr__(name, value)


sopds_cfg = SopdsConfig()
