"""Регрессия для `SynchronizationService._shorten_filename_for_path_limit()`
— обнаружено на реальной библиотеке (Микишин Фёдор / "Хан Батый и
десантники"): длинное название с подзаголовком-посвящением ("...
Альтернативная история с попаданцами. Посвящается курсантам военных
училищ СССР") давало итоговый путь синхронизации РОВНО в 260 символов —
`shutil.move()` падал с `[WinError 3] Системе не удается найти указанный
путь` (Windows MAX_PATH без включённой поддержки длинных путей), хотя обе
папки физически существовали. Ошибка ловилась в `_move_files()` и просто
считалась как `errors += 1` — файл так и оставался несинхронизированным,
без явного объяснения причины пользователю.
"""
from pathlib import Path

from fb2parser_core.synchronization import SynchronizationService


def _sync():
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.log_callback = None
    svc._log = lambda msg: None
    return svc


class TestShortenFilenameForPathLimit:
    def test_short_name_untouched(self, tmp_path):
        sync = _sync()
        name = "Автор - Серия. Название т. 1.fb2"
        result = sync._shorten_filename_for_path_limit(tmp_path, name)
        assert result == name

    def test_long_name_truncated_below_max_path(self, tmp_path):
        # Тот же образующий путь длиной ровно 260 символов, что и в реальном
        # случае (Микишин Фёдор): длинная папка + длинное имя файла.
        target_dir = tmp_path / ("d" * 100)
        long_name = (
            "Микишин Федор - Хан Батый и десантники. Книга 1. Выживание. "
            "Альтернативная история с попаданцами. Посвящается курсантам "
            "военных училищ СССР т. 1.fb2"
        )
        full_before = str(target_dir / long_name)
        assert len(full_before) > sync_max_path()

        result = _sync()._shorten_filename_for_path_limit(target_dir, long_name)
        full_after = str(target_dir / result)

        assert len(full_after) <= sync_max_path()
        assert result.endswith(".fb2")
        assert result != long_name

    def test_truncation_keeps_extension_and_marker(self, tmp_path):
        target_dir = tmp_path / ("d" * 20)
        long_name = "Автор - " + ("Очень длинное название серии и книги " * 5) + ".fb2"
        result = _sync()._shorten_filename_for_path_limit(target_dir, long_name)
        assert result.endswith(".fb2")
        assert "…" in result
        assert len(str(target_dir / result)) <= sync_max_path()


def sync_max_path() -> int:
    return SynchronizationService._MAX_PATH
