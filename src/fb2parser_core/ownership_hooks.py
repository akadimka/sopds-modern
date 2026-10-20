"""Учёт владельцев файлов библиотеки для операций fb2parser
(docs/multiuser-design.md, docs/multiuser-plan.md шаг 1.4).

fb2parser_core не зависит от Django: здесь — только точка вызова. Каждая
операция, которая кладёт файл в библиотеку, двигает, сливает или удаляет
его, сообщает об этом сюда абсолютными путями; при старте Django
приложение каталога подключает настоящий учёт (`register`,
opds_catalog/ownership.py). Без подключения (fb2parser_core сам по себе)
вызовы ничего не делают.

Пути вне библиотеки (исходные папки, папка нормализации) учёт пропускает
сам — вызывающему не нужно разбираться, библиотека это или нет.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Protocol, Tuple


class OwnershipHooks(Protocol):
    def assign(self, path: Path) -> None: ...
    def transfer(self, old: Path, new: Path) -> None: ...
    def merge(self, sources: Iterable[Path], target: Path, keep_sources: bool = False) -> None: ...
    def release(self, path: Path, file_exists: Optional[bool] = None) -> None: ...
    def owner_set_key(self, path: Path) -> Tuple[int, ...]: ...


class _NoHooks:
    def assign(self, path):
        pass

    def transfer(self, old, new):
        pass

    def merge(self, sources, target, keep_sources=False):
        pass

    def release(self, path, file_exists=None):
        pass

    def owner_set_key(self, path):
        return ()


_hooks: OwnershipHooks = _NoHooks()


def register(hooks: Optional[OwnershipHooks]) -> None:
    """Подключить учёт (None — отключить)."""
    global _hooks
    _hooks = hooks if hooks is not None else _NoHooks()


def assign(path) -> None:
    """Файл положили в библиотеку (синхронизация, ручной перенос)."""
    _hooks.assign(Path(path))


def transfer(old, new) -> None:
    """Файл переехал или переименован."""
    _hooks.transfer(Path(old), Path(new))


def merge(sources, target, keep_sources: bool = False) -> None:
    """Содержимое нескольких файлов собрано в один (сборник);
    `keep_sources` — исходники остаются на месте."""
    _hooks.merge([Path(s) for s in sources], Path(target), keep_sources)


def release(path, file_exists: Optional[bool] = None) -> None:
    """Файл удалён (`file_exists=False`) или от него отказались."""
    _hooks.release(Path(path), file_exists)


def owner_set_key(path) -> Tuple[int, ...]:
    """Набор владельцев файла — компилятор не сливает книги разных наборов."""
    return _hooks.owner_set_key(Path(path))
