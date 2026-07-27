"""Живой прогресс фоновых команд fetch_samlib_ratings / fetch_authortoday_ratings.

Эти команды — вечные (`while True`) процессы, запускаемые отдельно от веб-сервера
(systemd/cron/nohup), не через JobState-паттерн fb2parser_web (там job стартует
и завершается в рамках HTTP-запроса того же процесса). Здесь ситуация другая:
нужно, чтобы ЛЮБОЙ веб-воркер мог узнать, что сейчас делает независимо
запущенный процесс — отсюда просто общий (Django cache) key-value, без
семантики running/try_start.

Раньше о прогрессе судили только по счётчикам в БД (сколько строк уже есть
в SamlibRating/AuthorTodayRating) — это не отличает "работает, но медленно
из-за троттлинга на 10 минут" от "процесс не запущен вообще".
"""
from django.core.cache import cache
from django.utils import timezone

_TIMEOUT = 3 * 24 * 3600  # с большим запасом относительно SLEEP_IDLE (24ч)


def _key(source: str) -> str:
    return f"ratings_progress:{source}"


def set_progress(source: str, **fields) -> None:
    """Обновить прогресс для source ("samlib" | "authortoday").

    fields могут включать: status ("processing"|"sleeping"|"throttled"|"idle"),
    book_id, book_title, last_result ("found"|"not_found"|"error"),
    resume_at (datetime — когда ожидается следующее действие).
    """
    data = dict(fields)
    data["updated_at"] = timezone.now()
    cache.set(_key(source), data, _TIMEOUT)


def get_progress(source: str) -> dict:
    return cache.get(_key(source)) or {}
