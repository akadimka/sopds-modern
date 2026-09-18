"""Регрессия для `SopdsConfig._get_sm()` — docs/quality-roadmap.md, баг №100.

Найдено при архитектурном аудите: `_get_sm()` безусловно строила НОВЫЙ
`SettingsManager` (два `json.load()` + `copy.deepcopy()` всего дерева
настроек) на КАЖДЫЙ `config.SOPDS_*` атрибут — а
`SOPDSLocaleMiddleware` читает `config.SOPDS_LANGUAGE` на КАЖДОМ
запросе сайта.
"""
import json
import os

import pytest

import opds_catalog.sopds_config as sopds_config_module
from opds_catalog.sopds_config import SopdsConfig


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Подменяет _CONFIG_PATH на временный файл и сбрасывает кэш —
    иначе тест мутировал бы настоящий config.json пользователя."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"sopds": {"language": "en-US"}}), encoding="utf-8")

    monkeypatch.setattr(sopds_config_module, "_CONFIG_PATH", str(config_path))
    monkeypatch.setitem(sopds_config_module._sm_cache, "sm", None)
    monkeypatch.setitem(sopds_config_module._sm_cache, "config_mtime", None)
    monkeypatch.setitem(sopds_config_module._sm_cache, "app_settings_mtime", None)

    return config_path


class TestGetSmCaching:
    def test_repeated_reads_reuse_same_settings_manager_instance(self, isolated_config):
        cfg = SopdsConfig()
        assert cfg.SOPDS_LANGUAGE == "en-US"
        assert cfg.SOPDS_LANGUAGE == "en-US"
        assert cfg.SOPDS_AUTH is not None

        sm1 = sopds_config_module._get_sm()
        sm2 = sopds_config_module._get_sm()
        # Один и тот же объект — файл на диске не менялся между вызовами.
        assert sm1 is sm2

    def test_cache_invalidated_when_file_changes_on_disk(self, isolated_config):
        cfg = SopdsConfig()
        assert cfg.SOPDS_LANGUAGE == "en-US"

        # Файл меняется НЕ через сам SopdsConfig (например, отдельный
        # процесс/редактирование вручную) — новое содержимое + сдвигаем
        # mtime гарантированно вперёд (на некоторых ФС разрешение mtime
        # всего 1-2с — обычной записи может не хватить, чтобы значение
        # отличалось от закэшированного).
        isolated_config.write_text(json.dumps({"sopds": {"language": "ru-RU"}}), encoding="utf-8")
        new_mtime = os.path.getmtime(isolated_config) + 5
        os.utime(isolated_config, (new_mtime, new_mtime))

        assert cfg.SOPDS_LANGUAGE == "ru-RU"

    def test_write_through_setattr_is_immediately_visible(self, isolated_config):
        cfg = SopdsConfig()
        cfg.SOPDS_LANGUAGE = "de-DE"
        assert cfg.SOPDS_LANGUAGE == "de-DE"
