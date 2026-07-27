"""Управление фоновыми потоками сбора рейтингов (samlib/author.today) из веб-приложения.

Только для локального использования без systemd (см. DEPLOY.md) — если фичи
запущены как systemd-сервисы на сервере, НЕ подключайте автозапуск через
ready()/веб-хук здесь, иначе получится два независимых обходчика одновременно
(вдвое больше запросов к сайту и риск более агрессивного троттлинга/бана).

Поток стартует/останавливается по действию пользователя в /web/settings/
(галочка "Fetch ratings from Samizdat" / "Fetch likes from author.today" +
кнопка "Сохранить") — не при каждом запуске приложения.
"""
import threading
import time

from django.core.cache import cache
from django.core.management import call_command

_COMMAND_NAME = {
    "samlib": "fetch_samlib_ratings",
    "authortoday": "fetch_authortoday_ratings",
}

_STOP_TIMEOUT = 24 * 3600  # держим флаг остановки дольше самой длинной паузы (SLEEP_IDLE)

_lock = threading.RLock()  # reentrant: start_fetcher() locks then calls is_running(), which locks again
_threads: dict = {}  # source -> Thread (только текущий процесс — не переживает рестарт)


def _stop_key(source: str) -> str:
    return f"ratings_fetcher_stop:{source}"


def request_stop(source: str) -> None:
    """Попросить фоновый цикл остановиться. Проверяется на каждой итерации
    и во время "сна" между попытками (см. sleep_or_stop), так что остановка
    занимает секунды, а не до 24 часов (длина паузы idle-цикла)."""
    cache.set(_stop_key(source), True, _STOP_TIMEOUT)


def clear_stop(source: str) -> None:
    cache.delete(_stop_key(source))


def stop_requested(source: str) -> bool:
    return bool(cache.get(_stop_key(source), False))


def sleep_or_stop(source: str, seconds: float, chunk: float = 3.0) -> bool:
    """Спать `seconds`, но проверять request_stop() каждые `chunk` секунд.

    Returns:
        True если сон был прерван запросом на остановку (вызывающий код
        должен завершить цикл), False если истекло полное время без остановки.
    """
    remaining = seconds
    while remaining > 0:
        if stop_requested(source):
            return True
        time.sleep(min(chunk, remaining))
        remaining -= chunk
    return stop_requested(source)


def is_running(source: str) -> bool:
    with _lock:
        t = _threads.get(source)
        return bool(t and t.is_alive())


def start_fetcher(source: str) -> bool:
    """Запустить фоновый поток сбора рейтингов, если ещё не запущен.

    Returns True если поток был запущен этим вызовом, False если уже работал.
    """
    if source not in _COMMAND_NAME:
        raise ValueError(f"unknown ratings source: {source!r}")
    with _lock:
        if is_running(source):
            return False
        clear_stop(source)
        cmd_name = _COMMAND_NAME[source]

        def _run():
            try:
                call_command(cmd_name)
            except Exception:
                # Не роняем веб-процесс из-за ошибки в фоновом потоке —
                # достаточно того, что она видна в логах.
                import logging
                logging.getLogger(__name__).exception(
                    "ratings fetcher %s crashed", source
                )

        t = threading.Thread(target=_run, daemon=True, name=f"ratings-{source}")
        _threads[source] = t
        t.start()
        return True


def stop_fetcher(source: str) -> None:
    """Попросить фоновый поток остановиться (не ждёт завершения)."""
    request_stop(source)
