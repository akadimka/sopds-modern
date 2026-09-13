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


class TestShortenFilenameForFilesystemByteLimit:
    """Реальный случай (Демченко Антон / "Хольмградские истории"): имя
    файла всего 145 СИМВОЛОВ (комфортно короче MAX_PATH), но кириллица
    кодируется по 2 байта на символ в UTF-8 → 261 БАЙТ имени файла —
    `shutil.move()` падал с `[WinError 123] Синтаксическая ошибка в имени
    файла...` при итоговом пути всего 222 символа. Старая проверка (только
    по символам полного пути) не могла это поймать — лимит NAME_MAX (255
    байт на компонент пути) специфичен для файловой системы Samba-шары, не
    для Windows MAX_PATH.
    """

    def test_byte_limit_exceeded_even_with_short_char_count(self, tmp_path):
        # Короткая целевая папка — путь по символам далеко не подходит
        # к MAX_PATH, проблема именно в самом имени файла.
        name = (
            "Демченко Антон - Хольмградские истории. Человек для особых "
            "поручений. Самозванец по особому поручению. Беглец от особых "
            "поручений (сборник).fb2"
        )
        assert len(str(tmp_path / name)) < sync_max_path()
        assert len(name.encode('utf-8')) > SynchronizationService._MAX_NAME_BYTES

        result = _sync()._shorten_filename_for_path_limit(tmp_path, name)

        assert len(result.encode('utf-8')) <= SynchronizationService._MAX_NAME_BYTES
        assert result.endswith(".fb2")
        assert result != name

    def test_ascii_name_never_touched_by_byte_limit(self):
        # Чисто ASCII-имя такой же длины символов не превышает байтовый
        # лимит (1 символ = 1 байт) — не должно обрезаться. Короткая
        # искусственная папка (не реальный tmp_path — тот сам по себе может
        # быть достаточно длинным, чтобы упереться в MAX_PATH по символам,
        # что смешало бы два независимых лимита в одном тесте).
        name = "A" * 200 + ".fb2"
        result = _sync()._shorten_filename_for_path_limit(Path("C:/x"), name)
        assert result == name


def sync_max_path() -> int:
    return SynchronizationService._MAX_PATH
