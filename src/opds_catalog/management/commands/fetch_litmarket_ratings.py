"""Management command: fetch_litmarket_ratings

Медленно обходит книги без данных с litmarket.ru (или с устаревшими) и
пытается получить лайки/просмотры.

Как и на author.today, на litmarket.ru нет числовой оценки вида "X.XX из
N голосов" — только счётчик лайков ("rating-sticker") и приблизительный
счётчик просмотров ("views-sticker", часто в сокращённом виде "5k").
Лайки используются как основная метрика.

Поиск книги — через публичную страницу поиска (без авторизации):
https://litmarket.ru/search?query=...&type=book

Запуск:
    python manage.py fetch_litmarket_ratings
"""
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from difflib import SequenceMatcher

from django.core.management.base import BaseCommand
from django.utils import timezone


_AUTHOR_RE = re.compile(r'card-author">\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>')
_TITLE_RE = re.compile(r'card-name">\s*<a href="([^"]+)">([^<]+)</a>')
_LIKES_RE = re.compile(r'rating-sticker">\s*<i[^>]*></i>\s*(\d+)')
_VIEWS_RE = re.compile(r'views-sticker">\s*<i[^>]*></i>\s*([\d.,]+)\s*([kKmM]?)')


def _parse_approx_count(num_str, suffix):
    try:
        value = float(num_str.replace(',', '.'))
    except ValueError:
        return None
    if suffix.lower() == 'k':
        value *= 1_000
    elif suffix.lower() == 'm':
        value *= 1_000_000
    return int(value)


class Command(BaseCommand):
    help = "Получает лайки с litmarket.ru (фоновый процесс)"

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _STALE_DAYS = 14
    _SLEEP_MIN = 15
    _SLEEP_MAX = 30
    _SLEEP_THROTTLE = 600
    _SLEEP_IDLE = 86400
    _MATCH_THRESHOLD = 0.6  # минимальное сходство (автор+название), чтобы принять результат поиска

    def handle(self, *args, **options):
        from opds_catalog.sopds_config import sopds_cfg
        if not sopds_cfg.SOPDS_LITMARKET_RATING:
            self.stdout.write("SOPDS_LITMARKET_RATING=False — выходим.")
            return

        self.stdout.write("Запуск fetch_litmarket_ratings…")

        from opds_catalog.ratings_progress import set_progress
        from opds_catalog.ratings_fetchers import sleep_or_stop, stop_requested, clear_stop

        _SRC = "litmarket"

        while True:
            if stop_requested(_SRC):
                set_progress(_SRC, status="stopped")
                self.stdout.write("Остановлено по запросу.")
                clear_stop(_SRC)
                return

            book = self._next_book()
            if book is None:
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_IDLE)
                set_progress("litmarket", status="idle", resume_at=resume_at)
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                sleep_or_stop(_SRC, self._SLEEP_IDLE)
                continue

            set_progress("litmarket", status="processing",
                         book_id=book.id, book_title=book.title)
            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status, result = self._process_book(book)

            if status in (429, 503):
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_THROTTLE)
                set_progress("litmarket", status="throttled", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(
                    f"HTTP {status} — пауза {self._SLEEP_THROTTLE // 60} мин."
                )
                sleep_or_stop(_SRC, self._SLEEP_THROTTLE)
            else:
                delay = random.uniform(self._SLEEP_MIN, self._SLEEP_MAX)
                resume_at = timezone.now() + timedelta(seconds=delay)
                set_progress("litmarket", status="sleeping", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(f"  Пауза {delay:.0f} с.")
                sleep_or_stop(_SRC, delay)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _next_book(self):
        from opds_catalog.models import Book
        stale_cutoff = timezone.now() - timedelta(days=self._STALE_DAYS)
        without = Book.objects.exclude(litmarket_rating__isnull=False).first()
        if without:
            return without
        stale = Book.objects.filter(
            litmarket_rating__fetched_at__lt=stale_cutoff
        ).first()
        return stale

    def _process_book(self, book):
        author_name = ""
        first_author = book.authors.first()
        if first_author:
            author_name = first_author.full_name

        likes, reads, url, error = self._fetch_rating(author_name, book.title)

        if likes is not None:
            self.stdout.write(f"  Лайки: {likes}, просмотры: {reads}")
            result = "found"
        elif error:
            self.stdout.write("  Ошибка при получении данных")
            result = "error"
        else:
            self.stdout.write("  Книга не найдена на litmarket.ru")
            result = "not_found"

        self._save_rating(book, likes, reads, url, error)

        if error and url:
            return None, result
        return 200, result

    def _fetch_rating(self, author_name, title):
        """Ищет книгу по автору+названию, возвращает (likes, reads, url, error)."""
        query = f"{author_name} {title}".strip()
        search_url = "https://litmarket.ru/search?type=book&query=" + urllib.parse.quote(query)
        self.stdout.write(f"  GET {search_url}")

        try:
            html, status = self._fetch(search_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, None, search_url, True

        if status not in (None, 200):
            return None, None, search_url, True

        likes, reads, book_url = self._find_best_match(html, author_name, title)
        return likes, reads, book_url or search_url, False

    def _find_best_match(self, html, author_name, title):
        """Находит лучший результат поиска по сходству (автор, название)."""
        def _ratio(a, b):
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()

        best_likes, best_reads, best_url, best_score = None, None, None, 0.0
        for frag in html.split('<div class="item" data-book-id="')[1:]:
            m_author = _AUTHOR_RE.search(frag)
            m_title = _TITLE_RE.search(frag)
            if not m_title:
                continue
            found_url, found_title = m_title.groups()
            score = _ratio(found_title, title)
            if author_name and m_author:
                score = (score + _ratio(m_author.group(2), author_name)) / 2
            if score <= best_score:
                continue

            m_likes = _LIKES_RE.search(frag)
            m_views = _VIEWS_RE.search(frag)
            reads = _parse_approx_count(*m_views.groups()) if m_views else None

            best_score = score
            best_likes = int(m_likes.group(1)) if m_likes else 0
            best_reads = reads
            best_url = found_url

        if best_score >= self._MATCH_THRESHOLD:
            return best_likes, best_reads, best_url
        return None, None, None

    def _fetch(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self._UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                html = resp.read().decode("utf-8", errors="replace")
            return html, status
        except urllib.error.HTTPError as e:
            return "", e.code
        except Exception as exc:
            raise exc

    def _save_rating(self, book, likes, reads, url, fetch_error):
        from opds_catalog.models import LitmarketRating
        LitmarketRating.objects.update_or_create(
            book=book,
            defaults={
                "likes": likes or 0,
                "reads": reads,
                "litmarket_url": url or '',
                "fetched_at": timezone.now(),
                "fetch_error": fetch_error,
            },
        )
