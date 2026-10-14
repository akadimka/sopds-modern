"""Папки авторов, ждущие автокомпиляции после синхронизации (баг №150).

Синхронизация с автокомпиляцией сначала переносит файлы в библиотеку, потом
компилирует серии у затронутых авторов (touched_author_dirs). Список этих
папок жил только в памяти процесса: «Стоп» во время переноса или смерть
процесса до конца компиляции — и перенесённые тома навсегда оставались
отдельными файлами, следующая синхронизация о них не знала.

Теперь папка записывается в файл в момент переноса в неё первой книги и
удаляется из него только после завершённой компиляции. Следующая
синхронизация с автокомпиляцией доделывает оставшиеся. Файл лежит рядом с
sync.lock: читают и пишут его только под этой блокировкой.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Set


class PendingCompile:
    FILENAME = "pending_compile.json"

    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def beside(cls, lock_path) -> "PendingCompile":
        return cls(Path(lock_path).parent / cls.FILENAME)

    def load(self) -> Set[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {d for d in data if isinstance(d, str)} if isinstance(data, list) else set()

    def add(self, dirs: Iterable[str]) -> None:
        dirs = set(dirs)
        current = self.load()
        if not dirs - current:
            return
        self._write(current | dirs)

    def discard(self, dirs: Iterable[str]) -> None:
        current = self.load()
        left = current - set(dirs)
        if left != current:
            self._write(left)

    def _write(self, dirs: Set[str]) -> None:
        if not dirs:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(sorted(dirs), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
