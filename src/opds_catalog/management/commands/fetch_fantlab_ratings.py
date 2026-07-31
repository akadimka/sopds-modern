"""Management command: fetch_fantlab_ratings

Медленно обходит книги без рейтинга с fantlab.ru (или с устаревшим) и
пытается получить оценку.

В отличие от samlib.ru, отдельная навигация "автор → страница → секция"
не нужна: поиск (https://fantlab.ru/searchmain?searchstr=...) сразу
возвращает список произведений с готовым рейтингом ("X.XX (N)") прямо
в результатах, без второго запроса на страницу произведения.

Запуск:
    python manage.py fetch_fantlab_ratings
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


_WORK_BLOCK_RE = re.compile(
    r'<div class="one">\s*<div class="rating">\s*<span[^>]*>(?:<big>)?([\d.]+)(?:</big>)?\s*\((\d+)\)</span>\s*</div>\s*'
    r'<div class="cover[^"]*">\s*<a href="(/work\d+)">.*?</a>\s*</div>\s*'
    r'<div class="autor">(.*?)</div>\s*'
    r'<div class="title">\s*<a href="[^"]*"[^>]*>([^<]+)</a>',
    re.DOTALL,
)
_LINK_TEXT_RE = re.compile(r'>([^<]+)</a>')


class Command(BaseCommand):
    help = "Получает рейтинги книг с fantlab.ru (фоновый процесс)"

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
        if not sopds_cfg.SOPDS_FANTLAB_RATING:
            self.stdout.write("SOPDS_FANTLAB_RATING=False — выходим.")
            return

        self.stdout.write("Запуск fetch_fantlab_ratings…")

        from opds_catalog.ratings_progress import set_progress
        from opds_catalog.ratings_fetchers import sleep_or_stop, stop_requested, clear_stop

        _SRC = "fantlab"

        while True:
            if stop_requested(_SRC):
                set_progress(_SRC, status="stopped")
                self.stdout.write("Остановлено по запросу.")
                clear_stop(_SRC)
                return

            book = self._next_book()
            if book is None:
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_IDLE)
                set_progress("fantlab", status="idle", resume_at=resume_at)
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                sleep_or_stop(_SRC, self._SLEEP_IDLE)
                continue

            set_progress("fantlab", status="processing",
                         book_id=book.id, book_title=book.title)
            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status, result = self._process_book(book)

            if status in (429, 503):
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_THROTTLE)
                set_progress("fantlab", status="throttled", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(
                    f"HTTP {status} — пауза {self._SLEEP_THROTTLE // 60} мин."
                )
                sleep_or_stop(_SRC, self._SLEEP_THROTTLE)
            else:
                delay = random.uniform(self._SLEEP_MIN, self._SLEEP_MAX)
                resume_at = timezone.now() + timedelta(seconds=delay)
                set_progress("fantlab", status="sleeping", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(f"  Пауза {delay:.0f} с.")
                sleep_or_stop(_SRC, delay)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _next_book(self):
        from opds_catalog.models import Book
        stale_cutoff = timezone.now() - timedelta(days=self._STALE_DAYS)
        without = Book.objects.exclude(fantlab_rating__isnull=False).first()
        if without:
            return without
        stale = Book.objects.filter(
            fantlab_rating__fetched_at__lt=stale_cutoff
        ).first()
        return stale

    def _process_book(self, book):
        author_name = ""
        first_author = book.authors.first()
        if first_author:
            author_name = first_author.full_name

        rating, votes, url, error = self._fetch_rating(author_name, book.title)

        if rating is not None:
            self.stdout.write(f"  Рейтинг: {rating} ({votes} голосов)")
            result = "found"
        elif error:
            self.stdout.write("  Ошибка при получении рейтинга")
            result = "error"
        else:
            self.stdout.write("  Книга не найдена на fantlab.ru")
            result = "not_found"

        self._save_rating(book, rating, votes, url, error)

        if error and url:
            return None, result
        return 200, result

    def _fetch_rating(self, author_name, title):
        """Ищет книгу по автору+названию, возвращает (rating, votes, url, error)."""
        query = f"{author_name} {title}".strip()
        search_url = "https://fantlab.ru/searchmain?searchstr=" + urllib.parse.quote(query)
        self.stdout.write(f"  GET {search_url}")

        try:
            html, status = self._fetch(search_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, 0, search_url, True

        if status not in (None, 200):
            return None, 0, search_url, True

        return self._find_best_match(html, author_name, title) + (search_url, False)

    def _find_best_match(self, html, author_name, title):
        """Находит лучший результат поиска по сходству (автор, название)."""
        def _ratio(a, b):
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()

        best_rating, best_votes, best_score = None, 0, 0.0
        for rating_str, votes_str, work_path, autor_html, found_title in _WORK_BLOCK_RE.findall(html):
            found_authors = _LINK_TEXT_RE.findall(autor_html)
            score = _ratio(found_title, title)
            if author_name and found_authors:
                author_score = max(_ratio(a, author_name) for a in found_authors)
                score = (score + author_score) / 2
            if score > best_score:
                best_score = score
                try:
                    best_rating = float(rating_str)
                    best_votes = int(votes_str)
                except ValueError:
                    best_rating, best_votes = None, 0

        if best_score >= self._MATCH_THRESHOLD:
            return best_rating, best_votes
        return None, 0

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

    def _save_rating(self, book, rating, votes, url, fetch_error):
        from opds_catalog.models import FantlabRating
        FantlabRating.objects.update_or_create(
            book=book,
            defaults={
                "rating": rating,
                "votes": votes,
                "fantlab_url": url or '',
                "fetched_at": timezone.now(),
                "fetch_error": fetch_error,
            },
        )
