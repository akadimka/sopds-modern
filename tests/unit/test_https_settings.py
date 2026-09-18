"""Регрессия — docs/quality-roadmap.md, баг №98.

Найдено при архитектурном аудите: ни в одном settings-модуле не были
выставлены SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE/SECURE_SSL_REDIRECT
— django-axes защищает логин от подбора пароля, но сессионная/CSRF-кука
и сами учётные данные передавались бы без флага Secure даже при
включённом HTTPS. Настройки читают os.environ на уровне МОДУЛЯ при
импорте — единственный надёжный способ проверить разные значения не
ломая уже настроенный Django текущего тестового процесса — отдельный
подпроцесс с нужным окружением.
"""
import os
import subprocess
import sys

PYTHON = sys.executable
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")

_PROBE = (
    "import django; django.setup(); "
    "from django.conf import settings; "
    "print(settings.SECURE_SSL_REDIRECT); "
    "print(settings.SESSION_COOKIE_SECURE); "
    "print(settings.CSRF_COOKIE_SECURE); "
    "print(getattr(settings, 'SECURE_PROXY_SSL_HEADER', None))"
)


def _probe_settings(extra_env: dict) -> list[str]:
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "sopds.settings.test"
    env.update(extra_env)
    result = subprocess.run(
        [PYTHON, "-c", _PROBE],
        cwd=SRC_DIR, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()


class TestHttpsHardeningOptIn:
    def test_default_preserves_current_deployment_behaviour(self):
        """Без SOPDS_USE_HTTPS поведение НЕ должно меняться — DEPLOY.md
        документирует HTTP-only Apache reverse-proxy без TLS-шага."""
        env = os.environ.copy()
        env.pop("SOPDS_USE_HTTPS", None)
        lines = _probe_settings({})
        assert lines[0] == "False"  # SECURE_SSL_REDIRECT
        assert lines[1] == "False"  # SESSION_COOKIE_SECURE
        assert lines[2] == "False"  # CSRF_COOKIE_SECURE

    def test_sopds_use_https_enables_secure_cookies_and_redirect(self):
        lines = _probe_settings({"SOPDS_USE_HTTPS": "True"})
        assert lines[0] == "True"
        assert lines[1] == "True"
        assert lines[2] == "True"
        assert lines[3] == "None"  # SECURE_PROXY_SSL_HEADER не включён отдельно

    def test_trust_x_forwarded_proto_requires_https_flag_and_is_separate_opt_in(self):
        # Без SOPDS_USE_HTTPS — прокси-заголовок не должен включаться,
        # даже если явно попросили SOPDS_TRUST_X_FORWARDED_PROTO=True.
        lines = _probe_settings({"SOPDS_TRUST_X_FORWARDED_PROTO": "True"})
        assert lines[3] == "None"

        # С обоими флагами — заголовок включается.
        lines = _probe_settings({"SOPDS_USE_HTTPS": "True", "SOPDS_TRUST_X_FORWARDED_PROTO": "True"})
        assert lines[3] == "('HTTP_X_FORWARDED_PROTO', 'https')"
