"""Версии и выпуски sopds-modern (правила — CLAUDE.md, «Версии и выпуски»).

    python scripts/version.py current          текущая версия (pyproject.toml)
    python scripts/version.py next             следующая версия по коммитам с прошлого тега
    python scripts/version.py release          записать её: pyproject.toml, uv.lock, CHANGELOG.md
    python scripts/version.py release --set 2.27.001   то же с заданным номером
                                      [--no-changelog]  без раздела в CHANGELOG.md
    python scripts/version.py tag              тег vX.YY.ZZZ на HEAD (после коммита выпуска)

Номер меняется на выпуск (обновление сервера), по самому значимому
изменению с прошлого тега: несовместимое (ручные действия на сервере) —
X, новая функция — YY, исправление — ZZZ. Тип — из начала сообщения
коммита (`feat:`, `fix:`…); несовместимое — `feat!:`/`fix!:` или строка
`BREAKING:` в тексте коммита.
"""
from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sopds.version import display, parse, raw_version  # noqa: E402

MAJOR, MINOR, PATCH = "major", "minor", "patch"
MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "security", "perf", "data"}
TAG_RE = re.compile(r"^v\d+\.\d{2}\.\d{3}$")
_SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\([^)]*\))?(?P<bang>!)?:")
_RANK = {None: 0, PATCH: 1, MINOR: 2, MAJOR: 3}


def classify(subject: str, body: str = "") -> str | None:
    """Уровень изменения коммита: major / minor / patch / None (не влияет)."""
    m = _SUBJECT_RE.match(subject.strip())
    if (m and m.group("bang")) or re.search(r"^BREAKING\b", body, re.M):
        return MAJOR
    kind = m.group("type").lower() if m else ""
    if kind in MINOR_TYPES:
        return MINOR
    if kind in PATCH_TYPES:
        return PATCH
    return None


def bump(version: tuple, level: str | None) -> tuple:
    x, y, z = version
    if level == MAJOR:
        return x + 1, 0, 0
    if level == MINOR:
        return x, y + 1, 0
    if level == PATCH:
        return x, y, z + 1
    return version


def strongest(levels) -> str | None:
    return max(levels, key=_RANK.__getitem__, default=None)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def last_tag() -> str | None:
    tags = [t for t in _git("tag", "--merged", "HEAD").split() if TAG_RE.match(t)]
    return max(tags, key=parse, default=None)


def commits_since(tag: str | None) -> list:
    """[(hash, subject, body)] с тега до HEAD, старые первыми."""
    rng = [f"{tag}..HEAD"] if tag else ["HEAD"]
    out = _git("log", "--reverse", "--format=%h%x1f%s%x1f%b%x1e", *rng)
    return [tuple(rec.strip("\n").split("\x1f", 2)) for rec in out.split("\x1e") if rec.strip()]


def plan() -> tuple:
    """(тег, коммиты с уровнями, текущая версия, следующая версия)."""
    tag = last_tag()
    commits = [(h, s, b, classify(s, b)) for h, s, b in commits_since(tag)]
    try:
        current = parse(tag) if tag else parse(raw_version())
    except ValueError:              # номер до новой схемы (1.0.0RC1) — не переносится
        current = (0, 0, 0)
    return tag, commits, current, bump(current, strongest(c[3] for c in commits))


def _write_pyproject(version: tuple) -> None:
    canonical = ".".join(str(n) for n in version)
    path = ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    new = re.sub(r'(?m)^version = "[^"]*"', f'version = "{canonical}"', text, count=1)
    path.write_text(new, encoding="utf-8")
    lock = ROOT / "uv.lock"
    if lock.is_file():
        text = lock.read_text(encoding="utf-8")
        new = re.sub(r'(name = "sopds-modern"\nversion = ")[^"]*(")', rf"\g<1>{canonical}\g<2>", text, count=1)
        lock.write_text(new, encoding="utf-8")


def changelog_section(version: tuple, commits: list, date: str) -> str:
    groups = {MAJOR: [], MINOR: [], PATCH: []}
    for _h, subject, _b, level in commits:
        if level:
            groups[level].append(re.sub(r"^[A-Za-z]+(\([^)]*\))?!?:\s*", "", subject))
    titles = {MAJOR: "⚠ Требует действий на сервере", MINOR: "Добавлено", PATCH: "Исправлено"}
    lines = [f"## [v{display(version)}] — {date}", ""]
    for level in (MAJOR, MINOR, PATCH):
        if groups[level]:
            lines += [f"### {titles[level]}", ""] + [f"- {s}" for s in groups[level]] + [""]
    return "\n".join(lines) + "\n"


def _write_changelog(section: str) -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    i = text.find("\n## ")
    path.write_text(text[:i + 1] + section + text[i + 1:] if i >= 0 else text + "\n" + section, encoding="utf-8")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # консоль Windows — не UTF-8
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["current", "next", "release", "tag"])
    ap.add_argument("--set", dest="force", help="номер выпуска вместо вычисленного (X.YY.ZZZ)")
    ap.add_argument("--no-changelog", action="store_true", help="не дописывать CHANGELOG.md (раздел пишется вручную)")
    args = ap.parse_args(argv)

    if args.command == "current":
        print(f"v{display(raw_version())}")
        return 0

    if args.command == "tag":
        tag = f"v{display(raw_version())}"
        if tag in _git("tag", "--list", tag).split():
            print(f"тег {tag} уже есть")
            return 1
        _git("tag", "-a", tag, "-m", f"sopds-modern {tag}")
        print(f"тег {tag} поставлен на HEAD; отправить: git push origin {tag}")
        return 0

    tag, commits, current, proposed = plan()
    if args.command == "next":
        print(f"прошлый выпуск: {tag or 'нет тега'} (v{display(current)})")
        for h, subject, _b, level in commits:
            if level:
                print(f"  {level:6} {h} {subject}")
        print(f"следующая версия: v{display(proposed)}" if proposed != current else "изменений для выпуска нет")
        return 0

    version = parse(args.force) if args.force else proposed
    if version == current and not args.force:
        print("изменений для выпуска нет")
        return 1
    _write_pyproject(version)
    if not args.no_changelog:
        _write_changelog(changelog_section(version, commits, datetime.date.today().isoformat()))
    print(f"выпуск v{display(version)}: pyproject.toml, uv.lock, CHANGELOG.md обновлены.\n"
          "Дальше: проверить CHANGELOG, закоммитить, `python scripts/version.py tag`, отправить коммит и тег.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
