import os
from pathlib import Path

import environ
from django.utils.translation import gettext_lazy as _

# Инициализация для чтения переменных окружения из файла
# https://django-environ.readthedocs.io/en/latest/quickstart.html
env = environ.FileAwareEnv(DEBUG=(bool, False))

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

# Project version
VERSION = env("SOPDS_VERSION")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[".localhost", "127.0.0.1"])

# SERVER_LOG_LEVEL
SOPDS_SERVER_LOG_LEVEL = env("SOPDS_SERVER_LOG_LEVEL", default="WARNING")

# Application definition

INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "axes",
    "opds_catalog",
    "sopds_web_backend",
    "fb2parser_web",
    "django.contrib.admin",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.cache.UpdateCacheMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "opds_catalog.middleware.SOPDSLocaleMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ── Brute-force protection (django-axes) ──────────────────────────────────────
AXES_FAILURE_LIMIT = 5          # блокировка после 5 неудачных попыток
AXES_COOLOFF_TIME = 1           # разблокировка через 1 час
AXES_LOCKOUT_CALLABLE = None    # возвращает 403 (стандартное поведение)
AXES_RESET_ON_SUCCESS = True    # сброс счётчика после успешного входа
AXES_ENABLE_ADMIN = False       # не блокировать /admin/
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "sopds.urls.base"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "sopds_web_backend.processors.sopds_processor",
            ],
        },
    },
]

WSGI_APPLICATION = "sopds.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases
ENGINE = env("SOPDS_DB_ENGINE")
if ENGINE == "sqlite":
    default_database = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / env("SOPDS_DB_NAME", default="sopds.db"),
        # WAL-режим: позволяет веб-серверу и сканеру работать одновременно без блокировок
        "OPTIONS": {"timeout": 30},
    }
elif ENGINE == "postgres":
    default_database = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("SOPDS_DB_NAME"),
        "USER": env("SOPDS_DB_USER"),
        "PASSWORD": env("SOPDS_DB_PASSWORD"),
        "HOST": env("SOPDS_DB_HOST"),
        "PORT": env("SOPDS_DB_PORT"),
    }
else:
    raise ValueError(f"Unsupported SOPDS_DB_ENGINE: {ENGINE!r}. Use 'sqlite' or 'postgres'.")
DATABASES = {"default": default_database}

# Memcached — обязателен: gunicorn поднимает несколько worker-процессов
# (см. sopds.settings.gunicorn), у каждого своя память. Прогресс фоновых
# задач (скан, нормализация, синхронизация, компилятор — см.
# fb2parser_web.job_state) хранится здесь, а не в module-level словарях,
# иначе поллинг статуса может попасть в другой worker и увидеть "не запущено"
# посреди реально идущей задачи.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
        "LOCATION": env("MEMCACHED_LOCATION", default="127.0.0.1:11211"),
    },
}

# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        # "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGES = [
    ('en', 'English'),
    ('ru', 'Russian'),
]

LOCALE_PATHS = [
    os.path.join(BASE_DIR, "sopds/locale"),
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True
USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "static")

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_NAMES = {
    "AZ": _("Azerbaijani"),
    "SQ": _("Albanian"),
    "EN-US": _("American English"),
    "EN": _("English"),
    "HYE": _("Armenian"),
    "HY": _("Armenian"),
    "BA": _("Bashkir"),
    "BE": _("Belorussian"),
    "BG": _("Bulgarian"),
    "HU": _("Hungarian"),
    "VI": _("Vietnamese"),
    "EL": _("Greek"),
    "KA": _("Georgian"),
    "DA": _("Danishs"),
    "HE": _("Hebrew"),
    "IO": _("Ido"),
    "ID": _("Indonesian"),
    "GA": _("Irish"),
    "IS": _("Icelandic"),
    "ES": _("Spanish"),
    "IT": _("Italian"),
    "KK": _("Kazakh"),
    "CA": _("Katalan"),
    "ZH": _("Chinese"),
    "KO": _("Korean"),
    "LV": _("Latvian"),
    "LA": _("Latin"),
    "LT": _("Lithuanian"),
    "MK": _("Macedonian"),
    "DE": _("Germanian"),
    "NE": _("Nepali"),
    "NL": _("Dutch"),
    "NO": _("Norwegian"),
    "IE": _("Occidental"),
    "PL": _("Polish"),
    "PT": _("Portuguese"),
    "RO": _("Romainian"),
    "RU": _("Russian"),
    "RU~": _("Russian"),
    "SR": _("Serbian"),
    "SK": _("Slovak"),
    "TG": _("Tajik"),
    "TT": _("Tatar"),
    "TR": _("Turkish"),
    "UZ": _("Uzbek"),
    "UK": _("Ukrainian"),
    "FI": _("Finnish"),
    "FR": _("French"),
    "HR": _("Chroatian"),
    "CU": _("Church Slavonic"),
    "CS": _("Czech"),
    "CV": _("Chuvash"),
    "SV": _("Swedish"),
    "EO": _("Espseranto"),
    "ET": _("Estonian"),
    "SAH": _("Yakut"),
    "JA": _("Japanese"),
}

# Logger settings
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [P:{process:d}:{thread:d}] {levelname} [{name}:{funcName}:{lineno}]  {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} [{name}:{funcName}:{lineno}] {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
        "file": {
            "formatter": "verbose",
            "class": "logging.handlers.RotatingFileHandler",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 7,
            "filename": "log/sopds-ng.log",
            "encoding": "utf-8",
            "delay": True,
        },
        "scanner": {
            "formatter": "verbose",
            "class": "logging.handlers.RotatingFileHandler",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 3,
            "filename": "log/sopds-scaner.log",
            "encoding": "utf-8",
            "delay": True,
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "filters": ["require_debug_true"],
            "propagate": True,
        },
        "opds_catalog": {
            "handlers": ["console", "file"],
            "level": SOPDS_SERVER_LOG_LEVEL,
            "propagate": False,
        },
        "book_tools": {
            "handlers": ["console", "file"],
            "level": SOPDS_SERVER_LOG_LEVEL,
            "propagate": False,
        },
        "scanner": {
            "handlers": ["scanner"],
            "level": SOPDS_SERVER_LOG_LEVEL,
            "propagate": False,
        },
    },
}
