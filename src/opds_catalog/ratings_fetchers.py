"""Управление фоновыми потоками сбора рейтингов (samlib/author.today) из веб-приложения.

Только для локального использования без systemd (см. DEPLOY.md) — если фичи
запущены как systemd-сервисы на сервере, НЕ подключайте автозапуск через
ready()/веб-хук здесь, иначе получится два независимых обходчика одновременно
(вдвое больше запросов к сайту и риск более агрессивного троттлинга/бана).

Поток стартует/останавливается по действию пользователя в /web/settings/
(галочка "Fetch ratings from Samizdat" / "Fetch likes from author.today" +
кнопка "Сохранить") — не при каждом запуске приложения.

Разделение local/server режимов: systemd-юнит основного сервиса
(sopds-modern.service, см. DEPLOY.md шаг 11) выставляет переменную окружения
SOPDS_MANAGED_BY_SYSTEMD=1. Если она задана — считаем, что рейтинги уже
собираются отдельными systemd-сервисами (sopds-samlib/sopds-authortoday),
и start_fetcher()/stop_fetcher() отсюда не запускают/не останавливают
собственный поток: настройка просто сохраняется в config.json, а отдельный
сервис подхватывает её на следующей итерации (это уже проверяется на каждом
цикле, см. fetch_samlib_ratings/fetch_authortoday_ratings).
"""
import os
import threading
import time

from django.core.cache import cache
from django.core.management import call_command

_COMMAND_NAME = {
    "samlib": "fetch_samlib_ratings",
    "authortoday": "fetch_authortoday_ratings",
}


def is_systemd_managed() -> bool:
    """True если сервис развёрнут через systemd (см. DEPLOY.md шаг 11) —
    в этом режиме рейтинги собирают отдельные sopds-samlib/sopds-authortoday
    юниты, и веб-процессу нельзя запускать свой поток поверх них."""
    return os.environ.get("SOPDS_MANAGED_BY_SYSTEMD") == "1"

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

    На systemd-развёртывании (см. is_systemd_managed) — no-op: рейтинги там
    собирает отдельный systemd-сервис, запускать ещё один поток в веб-процессе
    поверх него нельзя (см. модульный docstring).

    Returns True если поток был запущен этим вызовом, False если уже работал
    (или если это systemd-развёртывание).
    """
    if source not in _COMMAND_NAME:
        raise ValueError(f"unknown ratings source: {source!r}")
    if is_systemd_managed():
        return False
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
    """Попросить фоновый поток остановиться (не ждёт завершения).

    На systemd-развёртывании — no-op. request_stop() пишет флаг в общий кеш
    (memcached), который проверяет и отдельный systemd-сервис — без этой
    проверки снятие галочки в Settings на сервере остановило бы уже
    работающий sopds-samlib/sopds-authortoday (а Restart=on-failure его не
    поднимет: команда завершается чисто, это не считается сбоем). Там
    управление — только через `systemctl stop/start`.
    """
    if is_systemd_managed():
        return
    request_stop(source)
