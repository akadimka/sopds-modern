"""Проверка путей к внешним конвертерам FB2 (fb2toepub/fb2tomobi/fb2toazw3).

Путь из настроек запускается сервером (`subprocess.Popen([path, src, dst])`
в dl.ConvertFB2) — если его можно задать из веб-интерфейса произвольно, это
выполнение любой программы на сервере. Через интерфейс разрешены только
известные конвертеры; другой путь администратор сервера может прописать
вручную в config.json.
"""
import os

ALLOWED_CONVERTER_NAMES = frozenset({
    "ebook-convert",  # Calibre — рекомендуется в DEPLOY.md
    "fb2c",
    "fb2epub",
    "fb2mobi",
    "fb2toepub",
    "fb2tomobi",
    "fb2toazw3",
    "kindlegen",
})
_ALLOWED_SUFFIXES = ("", ".exe", ".cmd", ".bat", ".sh", ".py")

# Зависший конвертер иначе навсегда занимал воркер.
CONVERT_TIMEOUT_SECONDS = 300


def converter_name_allowed(path: str) -> bool:
    name = os.path.basename(path).lower()
    return any(
        name.endswith(suffix) and name[: len(name) - len(suffix)] in ALLOWED_CONVERTER_NAMES
        for suffix in _ALLOWED_SUFFIXES
    )


def converter_path_error(path: str) -> str | None:
    """Причина, по которой путь нельзя сохранить из интерфейса, или None."""
    if not path:
        return None
    if not converter_name_allowed(path):
        return "unknown"
    if not os.path.isfile(path):
        return "missing"
    return None
