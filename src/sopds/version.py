"""Версия проекта (правила — CLAUDE.md, раздел «Версии и выпуски»).

Единственный источник — `version` в pyproject.toml. Там она без ведущих
нулей (PEP 440: `2.27.1`), показывается — в формате X.YY.ZZZ (`2.27.001`).
"""
import re
import tomllib
from pathlib import Path

_HERE = Path(__file__).resolve()
# Репозиторий: src/sopds/version.py → корень; пакет scripts/release.sh:
# содержимое src/ и pyproject.toml лежат в одной папке.
_PYPROJECT_CANDIDATES = (_HERE.parents[2] / "pyproject.toml", _HERE.parents[1] / "pyproject.toml")

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse(version: str) -> tuple:
    """`2.27.1`, `2.27.001`, `v2.27.001` → (2, 27, 1); иначе ValueError."""
    m = _VERSION_RE.match(version.strip())
    if not m:
        raise ValueError(f"not an X.YY.ZZZ version: {version!r}")
    return tuple(int(part) for part in m.groups())


def display(version) -> str:
    """(2, 27, 1) или `2.27.1` → `2.27.001`; нераспознанная строка — как есть."""
    try:
        x, y, z = version if isinstance(version, tuple) else parse(version)
    except ValueError:
        return str(version)
    return f"{x}.{y:02d}.{z:03d}"


def raw_version() -> str:
    for path in _PYPROJECT_CANDIDATES:
        if path.is_file():
            with path.open("rb") as f:
                return tomllib.load(f)["project"]["version"]
    return "0.0.0"


def display_version() -> str:
    return display(raw_version())
