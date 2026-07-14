"""Management command: fetch_samlib_ratings

Медленно обходит книги без рейтинга Самиздата (или устаревшим рейтингом)
и пытается получить оценку с samlib.ru.

Запуск:
    python manage.py fetch_samlib_ratings
"""
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Получает рейтинги книг с samlib.ru (фоновый процесс)"

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _STALE_DAYS = 7
    _SLEEP_MIN = 15
    _SLEEP_MAX = 30
    _SLEEP_THROTTLE = 600  # 10 минут при 429/503
    _SLEEP_IDLE = 86400    # 24 часа когда всё обработано

    def handle(self, *args, **options):
        from opds_catalog.sopds_config import sopds_cfg
        if not sopds_cfg.SOPDS_SAMLIB_RATING:
            self.stdout.write("SOPDS_SAMLIB_RATING=False — выходим.")
            return

        self.stdout.write("Запуск fetch_samlib_ratings…")
        while True:
            book = self._next_book()
            if book is None:
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                time.sleep(self._SLEEP_IDLE)
                continue

            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status = self._process_book(book)

            if status in (429, 503):
                self.stdout.write(
                    f"HTTP {status} — пауза {self._SLEEP_THROTTLE // 60} мин."
                )
                time.sleep(self._SLEEP_THROTTLE)
            else:
                delay = random.uniform(self._SLEEP_MIN, self._SLEEP_MAX)
                self.stdout.write(f"  Пауза {delay:.0f} с.")
                time.sleep(delay)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _next_book(self):
        from opds_catalog.models import Book, SamlibRating
        stale_cutoff = timezone.now() - timedelta(days=self._STALE_DAYS)
        # Книги без записи рейтинга
        without = Book.objects.exclude(samlib_rating__isnull=False).first()
        if without:
            return without
        # Книги со старым рейтингом
        stale = Book.objects.filter(
            samlib_rating__fetched_at__lt=stale_cutoff
        ).first()
        return stale

    def _process_book(self, book):
        from opds_catalog.models import SamlibRating

        author_name = ""
        first_author = book.authors.first()
        if first_author:
            author_name = first_author.full_name

        query = f"{author_name} {book.title}".strip()
        url = (
            "http://samlib.ru/cgi-bin/seek?"
            + urllib.parse.urlencode({"q": query, "type": "book"})
        )
        self.stdout.write(f"  GET {url}")

        try:
            html, status = self._fetch(url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка запроса: {exc}")
            self._save_rating(book, None, 0, url)
            return None

        if status not in (None, 200):
            return status

        rating, votes = self._parse_rating(html)
        if rating is not None:
            self.stdout.write(f"  Рейтинг: {rating} ({votes} голосов)")
        else:
            self.stdout.write("  Рейтинг не найден")

        self._save_rating(book, rating, votes, url)
        return 200

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

    def _parse_rating(self, html):
        """Ищет паттерн рейтинга в HTML Самиздата."""
        # Паттерн: «Оценка: X.XX (NNN)» или «X.XX*NNN»
        patterns = [
            r"[Оо]ценка[:\s]+(\d+(?:[.,]\d+)?)\s*\((\d+)\)",
            r"(\d+(?:[.,]\d+)?)\s*\*\s*(\d+)\s*оцен",
            r"<b>(\d+(?:[.,]\d+)?)</b>[^<]{0,30}(\d+)\s*оцен",
            r"rating[\"']?\s*[:\s]+(\d+(?:[.,]\d+)?)[^<]{0,40}(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                try:
                    rating = float(m.group(1).replace(",", "."))
                    votes = int(m.group(2))
                    return rating, votes
                except (ValueError, IndexError):
                    continue
        return None, 0

    def _save_rating(self, book, rating, votes, url):
        from opds_catalog.models import SamlibRating
        SamlibRating.objects.update_or_create(
            book=book,
            defaults={
                "rating": rating,
                "votes": votes,
                "samlib_url": url,
                "fetched_at": timezone.now(),
            },
        )
