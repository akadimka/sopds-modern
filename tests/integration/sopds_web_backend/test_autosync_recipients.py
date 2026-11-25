"""Кому идут оповещения автосинхронизации (docs/autosync-notifications-plan.md).

Получатели — учётные записи с привязанным Telegram: по умолчанию все
суперпользователи, после настройки — отмеченные. Решает (кнопки жанра)
только суперпользователь; назначенный пользователь — только сообщения.
«Chat id суперпользователя» из настроек получает оповещения, как раньше.
"""
import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from sopds_web_backend.models import TelegramLink
from sopds_web_backend.telegram_users import autosync_recipients, can_decide_autosync

pytestmark = pytest.mark.django_db

_STRONG = "Kx7#vQ2!mLp9Q"


def _linked(username, tg_id, superuser=False, active=True):
    if superuser:
        user = User.objects.create_superuser(username, password=_STRONG)
    else:
        user = User.objects.create_user(username, password=_STRONG)
    user.is_active = active
    user.save()
    TelegramLink.objects.create(user=user, telegram_id=tg_id, telegram_name=username)
    return user


def test_by_default_all_linked_superusers_and_the_superuser_chat():
    _linked("boss", 100, superuser=True)
    _linked("anna", 200)
    _linked("gone", 300, superuser=True, active=False)
    User.objects.create_superuser("nolink", password=_STRONG)
    assert sorted(autosync_recipients({"notify_users": None, "telegram_admin_chat": "42"})) == [
        ("100", True), ("42", True)]


def test_chosen_accounts_and_only_superusers_decide():
    boss = _linked("boss", 100, superuser=True)
    anna = _linked("anna", 200)
    _linked("other", 300, superuser=True)
    assert sorted(autosync_recipients({"notify_users": [boss.pk, anna.pk], "telegram_admin_chat": ""})) == [
        ("100", True), ("200", False)]
    assert autosync_recipients({"notify_users": [], "telegram_admin_chat": ""}) == []
    assert can_decide_autosync(100) and not can_decide_autosync(200) and not can_decide_autosync(999)


def test_settings_page_lists_linked_accounts_and_saves_the_choice(client, tmp_path, monkeypatch, settings):
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers

    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sopds": {"fb2toepub": "", "temp_dir": "", "language": "en-US"}}),
                      encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(config)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)
    boss = _linked("boss", 100, superuser=True)
    anna = _linked("anna", 200)
    User.objects.create_user("nolink", password=_STRONG)
    client.force_login(boss)

    page = client.get(reverse("web:settings") + "?tab=schedule").content.decode("utf-8")
    assert f'name="autosync_notify_users" value="{boss.pk}" checked' in page     # суперпользователь — по умолчанию
    assert f'name="autosync_notify_users" value="{anna.pk}">' in page
    block = page.split("autosync_notify_users_form")[1][:2000]
    assert "anna" in block and "nolink" not in block                              # без Telegram — нет в списке

    client.post(reverse("web:settings"), {"language": "ru-RU", "maxitems": "20", "fb2toepub": "", "temp_dir": "",
                                          "autosync_notify_users_form": "1",
                                          "autosync_notify_users": [str(anna.pk), "999999", "x"]})
    saved = json.loads(config.read_text(encoding="utf-8"))["autosync"]
    assert saved["notify_users"] == [anna.pk]                 # чужие и несуществующие id отброшены
