"""Защита учётной записи (разбор «что сделает злоумышленник с чужим паролем
или сессией», 2026-10-22):

- долговременный доступ — пароль для читалок, привязка Telegram, приложение
  второго фактора — только с паролем: украденной сессии мало, чтобы
  закрепиться в аккаунте;
- смена пароля (своя и суперпользователем) отзывает пароль для читалок,
  отвязывает Telegram (кроме единственного канала кода входа
  суперпользователя) и удаляет устройства, кроме текущего браузера;
- неверные коды второго фактора считаются на весь аккаунт: повторные входы
  с верным паролем не дают перебирать код;
- fb2parser и /admin/ — только суперпользователь (одного is_staff мало).
"""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

import sopds_web_backend.login_codes as lc
import sopds_web_backend.telegram_users as tu
from sopds_web_backend import opds_passwords, totp
from sopds_web_backend.models import OpdsPassword, TelegramLink, TotpDevice, UserDevice

PASSWORD = "Kx7#vQ2!mLp9"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    cfg = {"telegram_token": "", "telegram_proxy": "", "telegram_admin_chat": "", "admin_login_code": False}
    monkeypatch.setattr(lc, "_settings", lambda: cfg)
    monkeypatch.setattr(tu, "telegram", lambda service=None: (object(), None))
    monkeypatch.setattr(tu, "bot_username", lambda client, service: "lib_bot")
    return cfg


def _login(client, user, password=PASSWORD):
    return client.post(reverse("web:login"), {"username": user.username, "password": password})


def _profile(client, **data):
    return client.post(reverse("web:profile"), data).content.decode("utf-8")


@pytest.mark.parametrize("action", ["opds_new", "tg_code", "totp_start"])
def test_long_lived_access_needs_password(action):
    user = User.objects.create_user("reader", password=PASSWORD)
    client = Client()
    client.force_login(user)
    for password in ("", "wrong-password"):
        assert "Password is incorrect" in _profile(client, action=action, confirm_password=password)
    assert not OpdsPassword.objects.filter(user=user).exists()
    assert not TelegramLink.objects.filter(user=user).exclude(code="").exists()
    assert not TotpDevice.objects.filter(user=user).exists()
    _profile(client, action=action, confirm_password=PASSWORD)
    created = {"opds_new": OpdsPassword, "tg_code": TelegramLink, "totp_start": TotpDevice}[action]
    assert created.objects.filter(user=user).exists()


def test_unlinking_telegram_needs_password():
    user = User.objects.create_user("reader", password=PASSWORD)
    TelegramLink.objects.create(user=user, telegram_id=777, telegram_name="@reader_tg")
    client = Client()
    client.force_login(user)
    _profile(client, action="tg_unlink", confirm_password="")
    assert TelegramLink.objects.get(user=user).telegram_id == 777


def test_password_change_revokes_reader_password_and_other_devices():
    user = User.objects.create_user("reader", password=PASSWORD)
    other, mine = Client(), Client()
    _login(other, user)
    _login(mine, user)
    opds_passwords.generate(user)
    assert UserDevice.objects.filter(user=user).count() == 2
    new = "Zq8!wErT5yUi"
    _profile(mine, action="password", old_password=PASSWORD, new_password=new, confirm_password=new)
    assert not OpdsPassword.objects.filter(user=user).exists()
    [device] = UserDevice.objects.filter(user=user)                      # остался этот браузер
    assert mine.get(reverse("web:main")).status_code == 200
    assert other.get(reverse("web:main")).status_code == 302
    assert device.pk == mine.session.get("sopds_device_id")


def test_superuser_password_reset_revokes_everything_of_the_user():
    user = User.objects.create_user("reader", password=PASSWORD)
    _login(Client(), user)
    opds_passwords.generate(user)
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    root.post(reverse("web:user_edit", args=(user.pk,)), {"role": "user", "password": "Zq8!wErT5yUi"})
    assert not OpdsPassword.objects.filter(user=user).exists()
    assert not UserDevice.objects.filter(user=user).exists()
    assert root.get(reverse("web:users_list")).status_code == 200         # себе — не навредил


def test_superuser_changing_own_password_stays_signed_in():
    admin = User.objects.create_superuser("root", password=PASSWORD)
    root = Client()
    _login(root, admin)
    root.post(reverse("web:user_edit", args=(admin.pk,)), {"role": "superuser", "password": "Zq8!wErT5yUi"})
    assert root.get(reverse("web:users_list")).status_code == 200
    assert UserDevice.objects.filter(user=admin).count() == 1


def _with_app(user):
    from django.utils import timezone
    return TotpDevice.objects.create(user=user, secret=totp.new_secret(), confirmed=True, created=timezone.now())


@pytest.fixture
def kicked(monkeypatch):
    """Удаления из канала при отвязке — без сети."""
    calls = []
    monkeypatch.setattr(tu, "kick_from_channel", lambda tg_id: calls.append(tg_id) or True)
    return calls


def test_password_change_unlinks_telegram(kicked):
    """Злоумышленник с паролем привязал свой Telegram — смена пароля его
    отвязывает (иначе бот так и выдавал бы ему книги владельца)."""
    from sopds_web_backend.models import ActivityEvent
    user = User.objects.create_user("reader", password=PASSWORD)
    TelegramLink.objects.create(user=user, telegram_id=777, telegram_name="@intruder")
    client = Client()
    _login(client, user)
    new = "Zq8!wErT5yUi"
    page = _profile(client, action="password", old_password=PASSWORD, new_password=new, confirm_password=new)
    assert TelegramLink.objects.get(user=user).telegram_id is None
    assert kicked == [777]
    assert "stays linked" not in page
    event = ActivityEvent.objects.get(action="telegram_unlinked", user=user)
    assert event.details.get("reason") == "password_change"


def test_superuser_password_reset_unlinks_users_telegram(kicked):
    user = User.objects.create_user("reader", password=PASSWORD)
    TelegramLink.objects.create(user=user, telegram_id=777, telegram_name="@intruder")
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    root.post(reverse("web:user_edit", args=(user.pk,)), {"role": "user", "password": "Zq8!wErT5yUi"})
    assert TelegramLink.objects.get(user=user).telegram_id is None


def test_password_change_keeps_telegram_that_is_the_only_sign_in_code_channel(env, kicked):
    """Telegram-код — единственный второй шаг суперпользователя: отвязка
    закрыла бы вход ему самому. Привязка остаётся, профиль предупреждает."""
    env.update(admin_login_code=True, telegram_token="t")
    boss = User.objects.create_superuser("boss", password=SUPER_PASSWORD)
    TelegramLink.objects.create(user=boss, telegram_id=555, telegram_name="@boss_tg")
    client = Client()
    client.force_login(boss)
    new = "Boss-New-Password-1!"
    page = _profile(client, action="password", old_password=SUPER_PASSWORD, new_password=new, confirm_password=new)
    assert TelegramLink.objects.get(user=boss).telegram_id == 555
    assert kicked == []
    assert "stays linked" in page and "@boss_tg" in page


@pytest.mark.parametrize("other_way", ["app", "admin_chat"])
def test_password_change_unlinks_superuser_telegram_when_codes_have_another_way(env, kicked, other_way):
    env.update(admin_login_code=True, telegram_token="t")
    boss = User.objects.create_superuser("boss", password=SUPER_PASSWORD)
    TelegramLink.objects.create(user=boss, telegram_id=555, telegram_name="@boss_tg")
    if other_way == "app":
        _with_app(boss)
    else:
        env["telegram_admin_chat"] = "999"
    assert not lc.telegram_is_only_channel(boss)
    client = Client()
    client.force_login(boss)
    new = "Boss-New-Password-1!"
    _profile(client, action="password", old_password=SUPER_PASSWORD, new_password=new, confirm_password=new)
    assert TelegramLink.objects.get(user=boss).telegram_id is None
    assert kicked == [555]


def test_only_channel_does_not_depend_on_bot_token(env):
    """Токен убрали на время — отвязка всё равно закрыла бы вход, когда его вернут."""
    env.update(admin_login_code=True, telegram_token="", telegram_admin_chat="555")
    boss = User.objects.create_superuser("boss", password=SUPER_PASSWORD)
    TelegramLink.objects.create(user=boss, telegram_id=555, telegram_name="@boss_tg")
    assert lc.telegram_is_only_channel(boss)             # чат из настроек — тот же Telegram
    env["admin_login_code"] = False
    assert not lc.telegram_is_only_channel(boss)         # второго шага нет — отвязывать можно


def test_wrong_codes_counted_per_account_across_sign_ins():
    """Знающий пароль входит заново и получает новые 5 попыток — но счётчик
    на аккаунт после ACCOUNT_MAX_FAILURES закрывает ввод кода на час."""
    user = User.objects.create_user("reader", password=PASSWORD)
    device = _with_app(user)
    client = Client()
    wrong = 0
    while wrong < lc.ACCOUNT_MAX_FAILURES:
        assert _login(client, user).status_code == 302                    # на страницу кода
        for _ in range(min(lc.MAX_ATTEMPTS, lc.ACCOUNT_MAX_FAILURES - wrong)):
            client.post(reverse("web:login_code"), {"code": "000000"})
            wrong += 1
    assert lc.account_locked(user)
    page = _login(client, user)
    assert page.status_code == 403 and "Too many wrong codes for this account" in page.content.decode("utf-8")
    right = totp.code_at(device.secret, totp.current_step())
    assert client.post(reverse("web:login_code"), {"code": right}).status_code == 302   # ожидания нет — к паролю
    assert client.get(reverse("web:main")).status_code == 302                         # не вошёл


def test_profile_code_checks_share_the_account_counter():
    user = User.objects.create_user("reader", password=PASSWORD)
    device = _with_app(user)
    client = Client()
    client.force_login(user)
    for _ in range(lc.ACCOUNT_MAX_FAILURES):
        _profile(client, action="totp_disable", code="000000")
    right = totp.code_at(device.secret, totp.current_step())
    assert "Too many wrong codes for this account" in _profile(client, action="totp_disable", code=right)
    assert TotpDevice.objects.filter(user=user).exists()


def test_reader_password_only_for_accounts_with_second_factor(override_config):
    import base64
    user = User.objects.create_user("reader", password=PASSWORD)
    _with_app(user)
    reader_password = opds_passwords.generate(user)
    with override_config(SOPDS_AUTH=True):
        for password, status in ((PASSWORD, 401), (reader_password, 200)):
            auth = "Basic " + base64.b64encode(f"reader:{password}".encode()).decode()
            assert Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth).status_code == status


def test_staff_flag_alone_opens_neither_fb2parser_nor_admin():
    staff = User.objects.create_user("staff", password=PASSWORD, is_staff=True)
    client = Client()
    client.force_login(staff)
    assert client.get(reverse("fb2parser:dashboard")).status_code == 302
    assert client.get("/admin/").status_code == 302
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    assert root.get(reverse("fb2parser:dashboard")).status_code == 200
    assert root.get("/admin/").status_code == 200


def _reader_request(client, user, password):
    import base64
    token = base64.b64encode(f"{user.username}:{password}".encode()).decode()
    return client.get(reverse("opds:main"), HTTP_AUTHORIZATION=f"Basic {token}")


def test_own_sign_in_does_not_reset_guessing_of_another_password(override_config):
    """Подбор пароля суперпользователя: попытки чередуются с успешным входом
    в свою учётную запись (сайт или читалка) с того же IP. Раньше счётчик
    axes был по одному IP и обнулялся этим входом — подбор без предела."""
    victim = User.objects.create_superuser("boss", password="Boss-Password-123!")
    me = User.objects.create_user("mallory", password=PASSWORD)
    ip = {"REMOTE_ADDR": "10.9.9.9"}
    with override_config(SOPDS_AUTH=True):
        for round_ in range(2):
            for i in range(3):
                Client(**ip).post(reverse("web:login"), {"username": "boss", "password": f"bad{round_}{i}"})
            assert _login(Client(**ip), me).status_code == 302
            _reader_request(Client(**ip), me, PASSWORD)
    # 6 неверных попыток — пара «boss + IP» заблокирована, верный пароль не пускает.
    assert _login(Client(**ip), victim, "Boss-Password-123!").status_code != 302
    # С другого адреса владелец входит (блокировка — по паре, не по имени).
    assert _login(Client(REMOTE_ADDR="10.1.1.1"), victim, "Boss-Password-123!").status_code == 302
    # Свой вход атакующего не заблокирован.
    assert _login(Client(**ip), me).status_code == 302


def test_guessing_counted_regardless_of_username_case():
    victim = User.objects.create_superuser("boss", password="Boss-Password-123!")
    ip = {"REMOTE_ADDR": "10.9.9.8"}
    for i, name in enumerate(["boss", "Boss", "BOSS", " boss", "bOSS", "BosS"]):
        Client(**ip).post(reverse("web:login"), {"username": name, "password": f"bad{i}"})
    assert _login(Client(**ip), victim, "Boss-Password-123!").status_code != 302


def test_password_confirmation_is_rate_limited_per_account():
    """Подтверждение паролем внутри сессии (axes его не считает): после
    PASSWORD_MAX_FAILURES неверных — час не принимается и верный пароль, и
    счётчик общий для всех таких проверок учётной записи."""
    from sopds_web_backend.password_rules import PASSWORD_MAX_FAILURES
    user = User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    _login(client, user)
    for i in range(PASSWORD_MAX_FAILURES - 1):
        _profile(client, action="opds_new", confirm_password=f"guess{i}")
    _profile(client, action="delete", confirm_password="guess-last")       # другая проверка — тот же счётчик
    html = _profile(client, action="opds_new", confirm_password=PASSWORD)
    assert not OpdsPassword.objects.filter(user=user).exists()
    assert "Too many wrong passwords" in html
    _profile(client, action="password", old_password=PASSWORD,
             new_password="Nw8#pLq2!zXc", confirm_password="Nw8#pLq2!zXc")
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_right_password_clears_the_counter():
    from sopds_web_backend.password_rules import PASSWORD_MAX_FAILURES
    user = User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    _login(client, user)
    for round_ in range(2):
        for i in range(PASSWORD_MAX_FAILURES - 1):
            _profile(client, action="opds_new", confirm_password=f"guess{round_}{i}")
        _profile(client, action="opds_new", confirm_password=PASSWORD)
    assert OpdsPassword.objects.filter(user=user).exists()


SUPER_PASSWORD = "Boss-Password-123!"


def _superuser_client():
    boss = User.objects.create_superuser("boss", password=SUPER_PASSWORD)
    client = Client()
    client.force_login(boss)
    return boss, client


def test_superuser_own_password_from_users_table_needs_current_password():
    """Украденная сессия суперпользователя не меняет ему пароль из таблицы
    пользователей (владелец бы потерял доступ, а атакующий — остался)."""
    boss, client = _superuser_client()
    url = reverse("web:user_edit", args=[boss.pk])
    for confirm in ("", "wrong"):
        response = client.post(url, {"role": "superuser", "password": "Hijack-Password-999!",
                                     "confirm_password": confirm})
        assert response.status_code == 422
    boss.refresh_from_db()
    assert boss.check_password(SUPER_PASSWORD)
    client.post(url, {"role": "superuser", "password": "Hijack-Password-999!", "confirm_password": SUPER_PASSWORD})
    boss.refresh_from_db()
    assert boss.check_password("Hijack-Password-999!")


def test_new_superuser_and_promotion_need_password():
    _boss, client = _superuser_client()
    client.post(reverse("web:user_create"), {"username": "back", "password": "Back-Door-Pass-1!", "role": "superuser"})
    assert not User.objects.filter(username="back").exists()
    client.post(reverse("web:user_create"), {"username": "anna", "password": PASSWORD, "role": "user"})
    anna = User.objects.get(username="anna")                         # обычный пользователь — без пароля
    client.post(reverse("web:user_edit", args=[anna.pk]), {"role": "superuser"})
    anna.refresh_from_db()
    assert not anna.is_superuser
    client.post(reverse("web:user_edit", args=[anna.pk]), {"role": "user", "password": "Anna-New-Pw1!"})
    anna.refresh_from_db()
    assert anna.check_password("Anna-New-Pw1!")                      # пароль обычного — как раньше


def test_turning_off_the_second_factor_needs_password(monkeypatch, tmp_path):
    from fb2parser_core.settings_manager import SettingsManager
    from sopds_web_backend import views
    config = tmp_path / "config.json"
    config.write_text('{"autosync": {"admin_login_code": true}}', encoding="utf-8")
    monkeypatch.setattr(views, "_site_config_path", lambda: str(config))
    _boss, client = _superuser_client()
    client.post(reverse("web:users_settings"), {"devices_per_user": "0"})
    assert SettingsManager(str(config)).get_autosync_settings()["admin_login_code"] is True
    client.post(reverse("web:users_settings"), {"devices_per_user": "0", "confirm_password": SUPER_PASSWORD})
    assert SettingsManager(str(config)).get_autosync_settings()["admin_login_code"] is False


def test_superuser_chat_for_sign_in_codes_needs_password(monkeypatch, tmp_path):
    from fb2parser_core.settings_manager import SettingsManager
    from sopds_web_backend import views
    config = tmp_path / "config.json"
    config.write_text('{"autosync": {"telegram_admin_chat": "111"}}', encoding="utf-8")
    monkeypatch.setattr(views, "_site_config_path", lambda: str(config))
    _boss, client = _superuser_client()
    form = {"tab": "telegram", "autosync_telegram_admin_chat": "999", "autosync_telegram_channel": ""}
    client.post(reverse("web:settings"), form)
    assert SettingsManager(str(config)).get_autosync_settings()["telegram_admin_chat"] == "111"
    client.post(reverse("web:settings"), {**form, "confirm_password": SUPER_PASSWORD})
    assert SettingsManager(str(config)).get_autosync_settings()["telegram_admin_chat"] == "999"


def test_admin_cannot_change_passwords_roles_or_add_users():
    """/admin/ обходил правила сайта: смена пароля без отзыва паролей для
    читалок и без временного пароля, роль одним флагом, новый пользователь
    без обязательной смены пароля."""
    boss, client = _superuser_client()
    anna = User.objects.create_user("anna", password=PASSWORD)
    response = client.post(reverse("admin:auth_user_password_change", args=[anna.pk]),
                           {"password1": "Admin-Set-Pw1!", "password2": "Admin-Set-Pw1!"})
    assert response.status_code == 302 and response["Location"] == reverse("web:users_list")
    anna.refresh_from_db()
    assert anna.check_password(PASSWORD)
    assert client.get("/admin/password_change/").status_code == 302
    assert client.get(reverse("admin:auth_user_add")).status_code == 403
    change = client.get(reverse("admin:auth_user_change", args=[anna.pk])).content.decode()
    assert 'name="is_superuser"' not in change and 'name="is_staff"' not in change


def test_telegram_link_code_guessing_is_limited_per_telegram_account():
    """Код привязки — 8 символов, живёт сутки; без счётчика бот проверял
    сколько угодно кодов с одного Telegram (верный — чужой аккаунт в боте)."""
    user = User.objects.create_user("anna", password=PASSWORD)
    code = tu.new_link_code(user).code
    attacker = {"id": 666, "first_name": "M"}
    for i in range(tu.LINK_MAX_FAILURES):
        assert tu.link_by_code(f"WRONG{i:03d}", attacker) == (None, tu.ERR_BAD_CODE)
    assert tu.link_by_code(code, attacker) == (None, tu.ERR_TOO_MANY)
    link, error = tu.link_by_code(code, {"id": 777, "first_name": "A"})   # с другого Telegram — как раньше
    assert link is not None and link.telegram_id == 777
