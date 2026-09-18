"""Регрессия — docs/quality-roadmap.md, баг №88.

Найдено при архитектурном аудите: три места в fb2parser_core
конструировали `SettingsManager()` без обязательного позиционного
`config_path` (внутри `folder_author_parser/__init__.py` и
`gender_lookup.py`) — это ВСЕГДА бросает `TypeError`/`ImportError`
(в зависимости от контекста запуска), тихо проглатываемый широким
`except`, из-за чего пользовательские настройки
`collection_keywords`/`writer_occupation_qids` из `config.json`
никогда реально не применялись, только встроенные fallback-списки.

Тесты подменяют `SettingsManager` на фейк с контролируемым содержимым
конфига — так надёжнее и не зависит от текущего содержимого реального
`src/fb2_data/settings/config.json`.
"""
import fb2parser_core.settings_manager as settings_manager_module


class _FakeSettingsManagerForBlacklist:
    """Фейковый конфиг, который НЕ включает часть встроенных
    категорийных слов ("Цикл" и т.п.) — так и выглядит настоящий
    config.json на момент написания этого теста."""

    def __init__(self, config_path):
        self.config_path = config_path

    def get_list(self, key):
        assert key == "collection_keywords"
        # Намеренно НЕ включает "Цикл"/"Архив"/"Разное" — как в реальном
        # config.json. Зато содержит слово, которого нет во встроенном
        # списке — "компиляция".
        return ["компиляция"]


class TestFolderAuthorParserBlacklistUnion:
    def test_config_missing_builtin_word_does_not_unblacklist_it(self, monkeypatch):
        """Слово, которого нет в config.json, но есть во встроенном
        списке, должно остаться в чёрном списке (баг №88 — до фикса
        замена списка конфигом расблокировала такие папки как
        "авторов": "Цикл Иванова" начинало распознаваться как автор)."""
        monkeypatch.setattr(
            settings_manager_module, "SettingsManager", _FakeSettingsManagerForBlacklist
        )
        from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name

        assert parse_author_from_folder_name("Цикл Иванова") == ""
        assert parse_author_from_folder_name("Архив Петрова") == ""

    def test_config_word_extends_builtin_blacklist(self, monkeypatch):
        """Слово ТОЛЬКО из config.json (не во встроенном списке) должно
        реально работать — до фикса `SettingsManager()` без пути всегда
        падал, и config.json никогда не читался вообще."""
        monkeypatch.setattr(
            settings_manager_module, "SettingsManager", _FakeSettingsManagerForBlacklist
        )
        from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name

        assert parse_author_from_folder_name("Компиляция Иванова") == ""

    def test_normal_author_folder_unaffected(self, monkeypatch):
        monkeypatch.setattr(
            settings_manager_module, "SettingsManager", _FakeSettingsManagerForBlacklist
        )
        from fb2parser_core.passes.folder_author_parser import parse_author_from_folder_name

        assert parse_author_from_folder_name("Иванов Иван") == "Иванов Иван"


class _FakeSettingsManagerForGender:
    def __init__(self, config_path):
        self.config_path = config_path

    def get_writer_occupation_qids(self):
        return ["Q_FAKE_OCCUPATION"]


class TestGenderLookupServiceLoadsConfigWithoutExplicitSettings:
    def test_qids_from_config_applied_when_no_settings_passed(self, monkeypatch):
        """`GenderLookupService()` без явного `settings=` должен реально
        прочитать `writer_occupation_qids` из config.json, а не тихо
        падать на встроенный `_DEFAULT_WRITER_OCCUPATIONS` (баг №88)."""
        monkeypatch.setattr(
            settings_manager_module, "SettingsManager", _FakeSettingsManagerForGender
        )
        # _load_db_cache() трогает файл на диске рядом с исходником —
        # это не имеет отношения к проверяемому фиксу, глушим его.
        monkeypatch.setattr(
            "fb2parser_core.gender_lookup.GenderLookupService._load_db_cache",
            lambda self: None,
        )
        from fb2parser_core.gender_lookup import GenderLookupService

        svc = GenderLookupService()
        assert svc._writer_occupations == {"Q_FAKE_OCCUPATION"}
