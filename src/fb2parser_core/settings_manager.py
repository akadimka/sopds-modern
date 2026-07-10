"""
Settings Manager Module / Модуль управления настройками

Handles configuration and settings management.

/ Работа с конфигом и настройками.
"""
import json
from pathlib import Path
import copy

# Keys that belong to config.json (machine-specific, gitignored after first push).
# Everything else lives in app_settings.json (always in git).
_MACHINE_KEYS = frozenset({
    'library_path', 'last_scan_path', 'normalizer_folder', 'genres_file_path',
    'test_window_path', 'duplicate_finder_path', 'compiler_scan_dir', 'last_csv_dir',
    'window_sizes', 'genre_tree_state', 'generate_csv', 'settings_file_path',
    'sopds',
})


class SettingsManager:
    """
    Manages application settings and configuration.

    / Управляет настройками приложения и конфигурацией.
    """

    def __init__(self, config_path):
        """
        Initialize settings manager.

        / Инициализация менеджера настроек.
        """
        self.config_path = Path(config_path)
        self.app_settings_path = self.config_path.parent / 'app_settings.json'
        self.settings = {
            'library_path': '',
            'last_scan_path': '',
            'normalizer_folder': '',
            'genres_file_path': 'genres.xml',  # Путь к файлу жанров
            'genre_association_method': 'context_menu',
            'window_sizes': {},  # Для хранения размеров окон
            'generate_csv': False,  # Флаг генерации CSV файла
            'performance': {
                'enable_caching': True,  # Enable metadata caching
                'max_cache_age_days': 30,  # Cache validity period
                'use_sax_parser': True,  # Use SAX parser by default (faster)
            }
        }
        self._loaded_settings = None  # Для отслеживания оригинальных значений
        self.load()

    def load(self):
        """Load settings from config.json and app_settings.json."""
        if self.app_settings_path.exists():
            with open(self.app_settings_path, 'r', encoding='utf-8') as f:
                self.settings.update(json.load(f))
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.settings.update(json.load(f))
        self._loaded_settings = copy.deepcopy(self.settings)

    def _has_changes(self):
        """Проверить, были ли изменения в настройках / Check if settings have changed."""
        if self._loaded_settings is None:
            return True
        return self.settings != self._loaded_settings

    def save(self):
        """Save settings to config.json (machine keys) and app_settings.json (the rest)."""
        if not self._has_changes():
            return

        machine = {k: v for k, v in self.settings.items() if k in _MACHINE_KEYS}
        app = {k: v for k, v in self.settings.items() if k not in _MACHINE_KEYS}

        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(machine, f, ensure_ascii=False, indent=2)
        with open(self.app_settings_path, 'w', encoding='utf-8') as f:
            json.dump(app, f, ensure_ascii=False, indent=2)

        self._loaded_settings = copy.deepcopy(self.settings)

    def get(self, key: str, default=None):
        """Получить произвольное значение из настроек."""
        return self.settings.get(key, default)

    def set(self, key: str, value) -> None:
        """Сохранить произвольное значение в настройках."""
        self.settings[key] = value
        self.save()

    def set_library_path(self, path):
        """Set library path / Установить путь к библиотеке."""
        self.settings['library_path'] = path
        self.save()

    # --- Settings file path ---
    def get_settings_file_path(self) -> str:
        """Get stored path to the config (settings) file."""
        return self.settings.get('settings_file_path', '')

    def set_settings_file_path(self, path: str) -> None:
        """Set stored path to the config file and redirect future saves there."""
        self.settings['settings_file_path'] = path
        if path:
            self.config_path = Path(path)
            self.app_settings_path = self.config_path.parent / 'app_settings.json'
        self.save()

    def auto_init_file_paths(self) -> None:
        """Auto-detect project-root paths for config.json and genres.xml.

        Called once at startup.  Each path is set only when the current stored
        value is empty OR points to a non-existent file.
        """
        project_root = Path(__file__).resolve().parent

        # Settings file (config.json)
        stored_cfg = self.settings.get('settings_file_path', '')
        if not stored_cfg or not Path(stored_cfg).exists():
            candidate = project_root / 'config.json'
            self.settings['settings_file_path'] = str(candidate) if candidate.exists() else ''

        # Genres file (genres.xml)
        stored_genres = self.settings.get('genres_file_path', '')
        if not stored_genres or not Path(stored_genres).exists():
            candidate = project_root / 'genres.xml'
            self.settings['genres_file_path'] = str(candidate) if candidate.exists() else ''

        self.save()

        
    def get_genre_association_method(self):
        """Get genre association method / Получить метод ассоциации жанров."""
        return self.settings.get('genre_association_method', 'context_menu')
        
    def set_genre_association_method(self, method):
        """Set genre association method / Установить метод ассоциации жанров."""
        self.settings['genre_association_method'] = method
        self.save()

    def get_library_path(self):
        """Get library path / Получить путь к библиотеке."""
        return self.settings.get('library_path', '')
        
    def set_last_scan_path(self, path):
        """Set last scan path / Установить последний путь сканирования."""
        self.settings['last_scan_path'] = path
        self.save()
        
    def get_last_scan_path(self):
        """Get last scan path / Получить последний путь сканирования."""
        return self.settings.get('last_scan_path', '')

    def set_normalizer_folder(self, path: str) -> None:
        """Сохранить последнюю папку окна нормализации."""
        self.settings['normalizer_folder'] = path
        self.save()

    def get_normalizer_folder(self) -> str:
        """Получить последнюю папку окна нормализации."""
        return self.settings.get('normalizer_folder', '')

    def get_genres_file_path(self):
        """Get genres file path / Получить путь к файлу жанров."""
        genres_path = Path(self.settings.get('genres_file_path', 'genres.xml'))
        if genres_path.exists():
            return str(genres_path)

        local_genres = Path(__file__).resolve().parent / 'genres.xml'
        if local_genres.exists():
            self.set_genres_file_path(str(local_genres))
            return str(local_genres)

        return str(genres_path)
        
    def set_genres_file_path(self, path):
        """Set genres file path / Установить путь к файлу жанров."""
        self.settings['genres_file_path'] = path
        self.save()

    def get_folder_parse_limit(self):
        """Get folder parse limit / Получить предел количества папок при парсинге."""
        return self.settings.get('folder_parse_limit', 5)
        
    def set_folder_parse_limit(self, limit):
        """Set folder parse limit / Установить предел количества папок при парсинге."""
        try:
            self.settings['folder_parse_limit'] = int(limit)
        except (ValueError, TypeError):
            # Если не удаётся преобразовать в int, используем значение по умолчанию
            self.settings['folder_parse_limit'] = 5
        self.save()

    def get_generate_csv(self):
        """Get CSV generation flag / Получить флаг генерации CSV-файла."""
        return self.settings.get('generate_csv', False)
        
    def set_generate_csv(self, value):
        """Set CSV generation flag / Установить флаг генерации CSV-файла."""
        self.settings['generate_csv'] = bool(value)
        self.save()

    def get_test_window_path(self):
        """
        Get test window saved path.
        
        / Получает сохраненный путь для окна тестирования.
        """
        return self.settings.get('test_window_path', '')
        
    def set_test_window_path(self, path):
        """
        Set test window path.
        
        / Сохраняет путь для окна тестирования.
        """
        self.settings['test_window_path'] = path
        self.save()

    def set_window_size(self, window_name, width, height):
        """
        Save window size.
        
        / Сохраняет размеры окна.
        """
        if 'window_sizes' not in self.settings:
            self.settings['window_sizes'] = {}
        self.settings['window_sizes'][window_name] = {'width': width, 'height': height}
        self.save()

    def get_window_size(self, window_name):
        """
        Get saved window size.
        
        / Получает сохраненные размеры окна.
        """
        sizes = self.settings.get('window_sizes', {})
        return sizes.get(window_name, None)

    def set_window_geometry(self, window_name, geometry):
        """
        Save window geometry (size and position).
        
        / Сохраняет геометрию окна (размеры и позицию).
        """
        if 'window_sizes' not in self.settings:
            self.settings['window_sizes'] = {}
        self.settings['window_sizes'][window_name] = geometry
        self.save()

    def get_window_geometry(self, window_name):
        """
        Get saved window geometry.

        / Получает сохраненную геометрию окна.
        """
        sizes = self.settings.get('window_sizes', {})
        return sizes.get(window_name, None)

    def clear_secondary_window_geometries(self):
        """Remove saved geometry for all windows except 'main'.

        Called when the main window closes so secondary windows reopen near
        the main window (on the correct monitor) on the next launch.
        """
        sizes = self.settings.get('window_sizes', {})
        main_geom = sizes.get('main')
        self.settings['window_sizes'] = {'main': main_geom} if main_geom else {}
        self.save()

    def set_genre_tree_state(self, expanded_nodes):
        """
        Save genre tree state (expanded nodes).
        
        / Сохраняет состояние дерева жанров (развернутые узлы).
        """
        if 'genre_tree_state' not in self.settings:
            self.settings['genre_tree_state'] = {}
        self.settings['genre_tree_state']['expanded_nodes'] = list(expanded_nodes)
        self.save()

    def get_genre_tree_state(self):
        """
        Get saved genre tree state.
        
        / Получает сохраненное состояние дерева жанров.
        """
        state = self.settings.get('genre_tree_state', {})
        return set(state.get('expanded_nodes', []))

    # --- Blacklist helpers / Вспомогательные функции черного списка ---
    
    def get_filename_blacklist(self):
        """
        Get filename blacklist tokens.
        
        / Возвращает список токенов, используемых для проверки имени файла.
        """
        lst = self.settings.get('filename_blacklist')
        if lst is None:
            return []
        return list(lst)

    def set_filename_blacklist(self, lst):
        """
        Set filename blacklist and save config.
        
        / Устанавливает список токенов для filename_blacklist и сохраняет конфиг.
        """
        if lst is None:
            self.settings.pop('filename_blacklist', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            # store as list of strings
            self.settings['filename_blacklist'] = unique_list
        self.save()

    # Generic list helpers
    def list_list_keys(self):
        """Return top-level keys in settings whose value is a list."""
        return [k for k, v in self.settings.items() if isinstance(v, list)]

    def get_list(self, key):
        """Get a list value by key (or None if not present or not a list)."""
        v = self.settings.get(key)
        if isinstance(v, list):
            return list(v)
        return None

    def get_writer_occupation_qids(self) -> list:
        """Вернуть список Wikidata QID писательских профессий (P106).

        Хранится в config.json под ключом writer_occupation_qids.
        Каждый элемент — строка вида 'Q36180'.
        Используется GenderLookupService для мягкого приоритета:
        среди кандидатов-людей писатели проверяются первыми.
        """
        return self.settings.get('writer_occupation_qids') or []

    def get_name_particles(self) -> frozenset:
        """Вернуть frozenset частиц иностранных имён (де, ван, фон, ди…).

        Хранится в config.json под ключом name_particles.
        Используется в pass2_filename для валидации имён с частицами
        (напр. «Жиро де л Эн», «ван дер Берг»): если хотя бы одно слово
        имени является частицей — проверка наличия известного имени пропускается.
        """
        lst = self.settings.get('name_particles') or []
        return frozenset(p.lower() for p in lst)

    def get_no_series_folder_names(self) -> frozenset:
        """Вернуть frozenset нормализованных имён папок «без серии» из конфига.

        Нормализация: нижний регистр, ё→е.
        """
        lst = self.settings.get('no_series_folder_names')
        if not isinstance(lst, list):
            return frozenset()
        return frozenset(
            name.lower().replace('е́', 'е').replace('ё', 'е')
            for name in lst if name
        )

    def set_list(self, key, lst):
        """Set a top-level list value and save. If lst is None, remove the key.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop(key, None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            self.settings[key] = unique_list
        self.save()

    # --- Female names helpers ---
    def get_female_names(self):
        """Возвращает список женских имён."""
        lst = self.settings.get('female_names')
        if lst is None:
            return []
        return list(lst)

    def set_female_names(self, lst):
        """Устанавливает список женских имён и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('female_names', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            # store as list of strings
            self.settings['female_names'] = unique_list
        self.save()

    def add_female_name(self, name):
        """Добавляет женское имя в список (если его там ещё нет, case-insensitive)."""
        names = self.get_female_names()
        if name and not any(n.lower() == name.lower() for n in names):
            names.append(name)
            self.set_female_names(names)

    # --- Male names helpers ---
    def get_male_names(self):
        """Возвращает список мужских имён."""
        lst = self.settings.get('male_names')
        if lst is None:
            return []
        return list(lst)

    def set_male_names(self, lst):
        """Устанавливает список мужских имён и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('male_names', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            # store as list of strings
            self.settings['male_names'] = unique_list
        self.save()

    def add_male_name(self, name):
        """Добавляет мужское имя в список (если его там ещё нет, case-insensitive)."""
        names = self.get_male_names()
        if name and not any(n.lower() == name.lower() for n in names):
            names.append(name)
            self.set_male_names(names)

    # --- Genderize.io API key ---
    def get_genderize_api_key(self) -> str:
        """Вернуть API-ключ Genderize.io (пустая строка = бесплатный лимит)."""
        return str(self.settings.get('genderize_api_key', ''))

    def set_genderize_api_key(self, key: str) -> None:
        """Сохранить API-ключ Genderize.io."""
        self.settings['genderize_api_key'] = key.strip()
        self.save()

    # --- Service words helpers ---
    def get_service_words(self):
        """Возвращает список служебных слов."""
        lst = self.settings.get('service_words')
        if lst is None:
            return []
        return list(lst)

    def get_series_folder_blacklist(self):
        """Возвращает список организационных значений серий для очистки."""
        lst = self.settings.get('series_folder_blacklist')
        return list(lst) if lst else []

    def get_series_folder_prefixes_to_strip(self):
        """Возвращает список префиксов папок для обрезки из значений серий."""
        lst = self.settings.get('series_folder_prefixes_to_strip')
        return list(lst) if lst else []

    def get_author_subfolder_collections(self):
        """Папки-коллекции, чьи непосредственные подпапки всегда считаются авторскими."""
        lst = self.settings.get('author_subfolder_collections')
        return list(lst) if lst else []

    def get_genre_folder_prefixes(self):
        """Папки с жанровыми/издательскими префиксами — не являются авторами."""
        lst = self.settings.get('genre_folder_prefixes')
        return list(lst) if lst else []

    def get_author_folder_name_patterns(self):
        """Regex-паттерны для извлечения автора из имени папки (capture group 1)."""
        lst = self.settings.get('author_folder_name_patterns')
        return list(lst) if lst else []

    def set_service_words(self, lst):
        """Устанавливает список служебных слов и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('service_words', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            # store as list of strings
            self.settings['service_words'] = unique_list
        self.save()

    # --- Sequence patterns helpers ---
    def get_sequence_patterns(self):
        """Возвращает список шаблонов поиска серий."""
        lst = self.settings.get('sequence_patterns')
        if lst is None:
            return []
        return list(lst)

    def set_sequence_patterns(self, lst):
        """Устанавливает список шаблонов поиска серий и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('sequence_patterns', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            # store as list of strings
            self.settings['sequence_patterns'] = unique_list
        self.save()





    # --- Abbreviations preserve case helpers ---
    def get_abbreviations_preserve_case(self):
        """Возвращает список аббревиатур для сохранения кейса."""
        lst = self.settings.get('abbreviations_preserve_case')
        if lst is None:
            return []
        return list(lst)

    def set_abbreviations_preserve_case(self, lst):
        """Устанавливает список аббревиатур для сохранения кейса и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('abbreviations_preserve_case', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            self.settings['abbreviations_preserve_case'] = unique_list
        self.save()

    # --- Author initials and suffixes helpers ---
    def get_author_initials_and_suffixes(self):
        """Возвращает список инициалов и суффиксов авторов."""
        lst = self.settings.get('author_initials_and_suffixes')
        if lst is None:
            return []
        return list(lst)

    def set_author_initials_and_suffixes(self, lst):
        """Устанавливает список инициалов и суффиксов авторов и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('author_initials_and_suffixes', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            self.settings['author_initials_and_suffixes'] = unique_list
        self.save()

    # --- Series category words helpers ---
    def get_series_category_words(self):
        """Возвращает список категорийных слов для серий."""
        lst = self.settings.get('series_category_words')
        if lst is None:
            return []
        return list(lst)

    def set_series_category_words(self, lst):
        """Устанавливает список категорийных слов для серий и сохраняет конфиг.
        Removes duplicates (case-insensitive) while preserving order."""
        if lst is None:
            self.settings.pop('series_category_words', None)
        else:
            # Remove duplicates (case-insensitive) while preserving order
            seen = set()
            unique_list = []
            for item in lst:
                item_lower = str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique_list.append(str(item))
            self.settings['series_category_words'] = unique_list
        self.save()

    def get_author_series_patterns_in_files(self):
        """Возвращает список паттернов для поиска в имени файла."""
        lst = self.settings.get('author_series_patterns_in_files')
        if lst is None:
            return []
        return list(lst)

    def set_author_series_patterns_in_files(self, lst):
        """Устанавливает список паттернов для поиска в имени файла и сохраняет конфиг."""
        if lst is None:
            self.settings.pop('author_series_patterns_in_files', None)
        else:
            self.settings['author_series_patterns_in_files'] = list(lst)
        self.save()

    def get_author_series_patterns_in_folders(self):
        """Возвращает список паттернов для поиска в имени папки."""
        lst = self.settings.get('author_series_patterns_in_folders')
        if lst is None:
            return []
        return list(lst)

    def set_author_series_patterns_in_folders(self, lst):
        """Устанавливает список паттернов для поиска в имени папки и сохраняет конфиг."""
        if lst is None:
            self.settings.pop('author_series_patterns_in_folders', None)
        else:
            self.settings['author_series_patterns_in_folders'] = list(lst)
        self.save()

    def get_author_name_patterns(self):
        """Возвращает список паттернов для парсинга имени автора."""
        lst = self.settings.get('author_name_patterns')
        if lst is None:
            return []
        return list(lst)

    def set_author_name_patterns(self, lst):
        """Устанавливает список паттернов для парсинга имени автора и сохраняет конфиг."""
        if lst is None:
            self.settings.pop('author_name_patterns', None)
        else:
            self.settings['author_name_patterns'] = list(lst)
        self.save()

    # --- Author surname conversions helpers ---
    def get_author_surname_conversions(self):
        """Возвращает словарь конвертаций фамилий авторов (оригинальная -> конвертированная)."""
        d = self.settings.get('author_surname_conversions')
        if d is None:
            return {}
        if isinstance(d, dict):
            return dict(d)
        return {}

    def set_author_surname_conversions(self, d):
        """Устанавливает словарь конвертаций фамилий авторов и сохраняет конфиг."""
        if d is None:
            self.settings.pop('author_surname_conversions', None)
        else:
            self.settings['author_surname_conversions'] = dict(d)
        self.save()

    def add_author_surname_conversion(self, from_surname, to_surname):
        """Добавляет конвертацию фамилии."""
        conversions = self.get_author_surname_conversions()
        if from_surname and to_surname:
            conversions[from_surname] = to_surname
            self.set_author_surname_conversions(conversions)

    def remove_author_surname_conversion(self, from_surname):
        """Удаляет конвертацию фамилии."""
        conversions = self.get_author_surname_conversions()
        if from_surname in conversions:
            del conversions[from_surname]
            self.set_author_surname_conversions(conversions)
