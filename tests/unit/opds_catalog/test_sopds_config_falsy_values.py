"""`SopdsConfig.__getattr__` подменял любое ложное значение умолчанием
(`if not val: return default`): False и 0 из config.json игнорировались —
auth/doubles_hide/zipscan/delete_logical… нельзя было выключить, а
расписание на понедельник (scan_shed_dow=0) превращалось в «каждый день».
Страница настроек при этом показывала сохранённое False.

Тесты, подменяющие config через override_config, этого не ловили — они
пишут прямо в __dict__ в обход __getattr__.
"""
import json

import pytest

import opds_catalog.sopds_config as sopds_config_module
from opds_catalog.sopds_config import SopdsConfig


@pytest.fixture
def cfg_with(tmp_path, monkeypatch):
    def _make(sopds: dict) -> SopdsConfig:
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"sopds": sopds}), encoding="utf-8")
        monkeypatch.setattr(sopds_config_module, "_CONFIG_PATH", str(config_path))
        monkeypatch.setitem(sopds_config_module._sm_cache, "sm", None)
        monkeypatch.setitem(sopds_config_module._sm_cache, "config_mtime", None)
        monkeypatch.setitem(sopds_config_module._sm_cache, "app_settings_mtime", None)
        return SopdsConfig()
    return _make


@pytest.mark.parametrize("attr, key", [
    ("SOPDS_AUTH", "auth"),
    ("SOPDS_ALPHABET_MENU", "alphabet_menu"),
    ("SOPDS_DOUBLES_HIDE", "doubles_hide"),
    ("SOPDS_ZIPSCAN", "zipscan"),
    ("SOPDS_INPX_SKIP_UNCHANGED", "inpx_skip_unchanged"),
    ("SOPDS_DELETE_LOGICAL", "delete_logical"),
])
def test_true_by_default_setting_can_be_switched_off(cfg_with, attr, key):
    assert getattr(cfg_with({key: False}), attr) is False


def test_monday_schedule_is_kept(cfg_with):
    assert cfg_with({"scan_shed_dow": 0}).SOPDS_SCAN_SHED_DOW == 0


def test_zero_debounce_is_kept(cfg_with):
    assert cfg_with({"watch_debounce_seconds": 0}).SOPDS_WATCH_DEBOUNCE_SECONDS == 0


def test_missing_and_empty_values_fall_back_to_defaults(cfg_with):
    cfg = cfg_with({"book_extensions": "  ", "language": None})
    assert cfg.SOPDS_AUTH is True
    assert cfg.SOPDS_BOOK_EXTENSIONS == ".pdf .djvu .fb2 .epub .mobi"
    assert cfg.SOPDS_LANGUAGE == "en-US"


def test_zero_page_size_falls_back_to_default(cfg_with):
    cfg = cfg_with({"maxitems": 0, "splititems": 0})
    assert cfg.SOPDS_MAXITEMS == 10
    assert cfg.SOPDS_SPLITITEMS == 100
