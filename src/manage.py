#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

# На Windows пересоздаём процесс с PYTHONUTF8=1, если ещё не установлено.
# Это единственный надёжный способ сделать stdin/stdout/stderr UTF-8 до инициализации logging.
if sys.platform == "win32" and not os.environ.get("PYTHONUTF8"):
    os.environ["PYTHONUTF8"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sopds.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
