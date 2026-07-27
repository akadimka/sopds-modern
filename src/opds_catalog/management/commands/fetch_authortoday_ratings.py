"""Management command: fetch_authortoday_ratings

Медленно обходит книги без данных с author.today (или с устаревшими)
и пытается получить лайки/награды/просмотры.

В отличие от samlib.ru, на author.today нет числовой оценки вида
"X.XX из N голосов" — есть только лайки (likeCount), платные "награды"
от читателей и счётчик просмотров. Лайки используются как основная
метрика (ближе всего по смыслу к оценке качества), awards/reads —
дополнительный контекст.

Поиск книги — через публичную страницу поиска (без авторизации и без
API-токена): https://author.today/search?category=works&q=...

Запуск:
    python manage.py fetch_authortoday_ratings
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


_SEARCH_RESULT_RE = re.compile(
    r'<div class="book-title">\s*<a[^>]+href="(/work/\d+)"[^>]*>(.*?)</a>.*?'
    r'<div class="book-author"><a[^>]*>(.*?)</a></div>',
    re.DOTALL,
)
_TAG_RE = re.compile(r'<[^>]+>')
# Совпадения подсвечены как <em class='searched-item'>Слово</em> — снимаем теги.
_LIKES_RE = re.compile(r'likeCount:\s*(\d+)')
_READS_RE = re.compile(r'data-hint="[^"]*?(\d[\d\s\xa0]*)"[^>]*><i class="icon-eye"')
_AWARDS_RE = re.compile(r'icon-gift"></i>\s*(?:Награды|Award[s]?)\s*(\d+)')


def _clean(html_fragment: str) -> str:
    """Убрать HTML-теги (напр. <em class='searched-item'>) и схлопнуть пробелы."""
    text = _TAG_RE.sub('', html_fragment).strip()
    return re.sub(r'\s+', ' ', text)


class Command(BaseCommand):
    help = "Получает лайки/награды с author.today (фоновый процесс)"

    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _STALE_DAYS = 14
    _SLEEP_MIN = 15
    _SLEEP_MAX = 30
    _SLEEP_THROTTLE = 600   # 10 минут при 429/503 (у author.today ~20 запросов/мин на IP)
    _SLEEP_IDLE = 86400     # 24 часа когда всё обработано
    _MATCH_THRESHOLD = 0.6  # минимальное сходство (автор+название) чтобы принять результат поиска

    def handle(self, *args, **options):
        from opds_catalog.sopds_config import sopds_cfg
        if not sopds_cfg.SOPDS_AUTHORTODAY_RATING:
            self.stdout.write("SOPDS_AUTHORTODAY_RATING=False — выходим.")
            return

        self.stdout.write("Запуск fetch_authortoday_ratings…")

        from opds_catalog.ratings_progress import set_progress

        while True:
            book = self._next_book()
            if book is None:
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_IDLE)
                set_progress("authortoday", status="idle", resume_at=resume_at)
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                time.sleep(self._SLEEP_IDLE)
                continue

            set_progress("authortoday", status="processing",
                         book_id=book.id, book_title=book.title)
            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status, result = self._process_book(book)

            if status in (429, 503):
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_THROTTLE)
                set_progress("authortoday", status="throttled", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(
                    f"HTTP {status} — пауза {self._SLEEP_THROTTLE // 60} мин."
                )
                time.sleep(self._SLEEP_THROTTLE)
            else:
                delay = random.uniform(self._SLEEP_MIN, self._SLEEP_MAX)
                resume_at = timezone.now() + timedelta(seconds=delay)
                set_progress("authortoday", status="sleeping", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(f"  Пауза {delay:.0f} с.")
                time.sleep(delay)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _next_book(self):
        from opds_catalog.models import Book
        stale_cutoff = timezone.now() - timedelta(days=self._STALE_DAYS)
        without = Book.objects.exclude(authortoday_rating__isnull=False).first()
        if without:
            return without
        stale = Book.objects.filter(
            authortoday_rating__fetched_at__lt=stale_cutoff
        ).first()
        return stale

    def _process_book(self, book):
        author_name = ""
        first_author = book.authors.first()
        if first_author:
            author_name = first_author.full_name

        likes, awards, reads, url, error = self._fetch_rating(author_name, book.title)

        if likes is not None:
            self.stdout.write(f"  Лайки: {likes}, награды: {awards}, просмотры: {reads}")
            result = "found"
        elif error:
            self.stdout.write("  Ошибка при получении данных")
            result = "error"
        else:
            self.stdout.write("  Книга не найдена на author.today")
            result = "not_found"

        self._save_rating(book, likes, awards, reads, url, error)

        # Detect throttle HTTP status
        if error and url:
            return None, result
        return 200, result

    def _fetch_rating(self, author_name, title):
        """Ищет книгу по автору+названию, возвращает (likes, awards, reads, url, error)."""
        query = f"{author_name} {title}".strip()
        search_url = "https://author.today/search?category=works&q=" + urllib.parse.quote(query)
        self.stdout.write(f"  GET {search_url}")

        try:
            html, status = self._fetch(search_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, None, None, search_url, True

        if status not in (None, 200):
            return None, None, None, search_url, True

        work_path = self._find_best_match(html, author_name, title)
        if not work_path:
            return None, None, None, search_url, False

        work_url = "https://author.today" + work_path
        self.stdout.write(f"  GET {work_url}")
        try:
            work_html, status2 = self._fetch(work_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, None, None, work_url, True

        if status2 not in (None, 200):
            return None, None, None, work_url, True

        likes, awards, reads = self._parse_stats(work_html)
        return likes, awards, reads, work_url, False

    def _find_best_match(self, html, author_name, title):
        """Находит лучший результат поиска по сходству (автор, название)."""
        def _ratio(a, b):
            return SequenceMatcher(None, a.lower(), b.lower()).ratio()

        best_path, best_score = None, 0.0
        for work_path, raw_title, raw_author in _SEARCH_RESULT_RE.findall(html):
            found_title = _clean(raw_title)
            found_author = _clean(raw_author)
            score = _ratio(found_title, title)
            if author_name:
                score = (score + _ratio(found_author, author_name)) / 2
            if score > best_score:
                best_score, best_path = score, work_path

        if best_score >= self._MATCH_THRESHOLD:
            return best_path
        return None

    def _parse_stats(self, html):
        m_likes = _LIKES_RE.search(html)
        likes = int(m_likes.group(1)) if m_likes else None

        m_awards = _AWARDS_RE.search(html)
        awards = int(m_awards.group(1)) if m_awards else None

        m_reads = _READS_RE.search(html)
        if m_reads:
            reads_str = m_reads.group(1).replace(' ', '').replace('\xa0', '')
            reads = int(reads_str) if reads_str.isdigit() else None
        else:
            reads = None

        return likes, awards, reads

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

    def _save_rating(self, book, likes, awards, reads, url, fetch_error):
        from opds_catalog.models import AuthorTodayRating
        AuthorTodayRating.objects.update_or_create(
            book=book,
            defaults={
                "likes": likes or 0,
                "awards": awards,
                "reads": reads,
                "work_url": url or '',
                "fetched_at": timezone.now(),
                "fetch_error": fetch_error,
            },
        )
