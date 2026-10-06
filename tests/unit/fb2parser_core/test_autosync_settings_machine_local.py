"""Настройки автосинхронизации — только в config.json (машинно-локальный).

SettingsManager.save() раскладывает ключи: _MACHINE_KEYS — в config.json
(в .gitignore), всё остальное — в app_settings.json, который коммитится.
Раздел autosync содержит путь к папке наблюдения и токен Telegram-бота —
в git он попасть не должен.
"""
import json

from fb2parser_core.settings_manager import SettingsManager


def test_autosync_settings_saved_to_config_json_only(tmp_path):
    config = tmp_path / "config.json"
    app = tmp_path / "app_settings.json"
    config.write_text("{}", encoding="utf-8")
    app.write_text('{"genre_priority_order": ["Фантастика"]}', encoding="utf-8")

    SettingsManager(str(config)).set_autosync_settings(
        {"mode": "auto", "watch_folder": "/mnt/in", "telegram_token": "SECRET", "unknown": 1})

    assert json.loads(config.read_text(encoding="utf-8"))["autosync"]["telegram_token"] == "SECRET"
    assert "SECRET" not in app.read_text(encoding="utf-8")
    loaded = SettingsManager(str(config)).get_autosync_settings()
    assert loaded["mode"] == "auto" and loaded["watch_folder"] == "/mnt/in"
    assert loaded["confidence"] == 0.8  # умолчание для незаданного ключа
    assert "unknown" not in loaded


def test_save_keeps_key_order_of_shared_app_settings(tmp_path):
    # иначе каждое сохранение настроек переставляет ключи файла, который коммитится
    config = tmp_path / "config.json"
    app = tmp_path / "app_settings.json"
    config.write_text('{"library_path": "/lib"}', encoding="utf-8")
    original = '{\n  "genre_association_method": "context_menu",\n  "performance": {\n    "enable_caching": true\n  }\n}'
    app.write_text(original, encoding="utf-8")

    SettingsManager(str(config)).set_autosync_settings({"mode": "dry_run"})

    keys = list(json.loads(app.read_text(encoding="utf-8")))
    assert keys[:2] == ["genre_association_method", "performance"]
