"""Management command: fetch_samlib_ratings

Медленно обходит книги без рейтинга Самиздата (или устаревшим рейтингом)
и пытается получить оценку с samlib.ru.

Запуск:
    python manage.py fetch_samlib_ratings
"""
import json
import random
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import timedelta
from io import BytesIO

from django.core.management.base import BaseCommand
from django.utils import timezone


_SERIES_WORDS = [
    'Дилогия', 'Трилогия', 'Тетралогия', 'Пенталогия', 'Гексалогия',
    'Гепталогия', 'Окталогия', 'Ноналогия', 'Декалогия',
]
_COMPILATION_RE = re.compile(
    r'(?:' + '|'.join(_SERIES_WORDS) + r')|в\s+\d+\s+книгах|книги?\s+\d+[-–—]\d+|'
    r'т\.\s*\d+[-–—]\d+|компилян|компиляц|сборник|omnibus|антолог',
    re.IGNORECASE | re.UNICODE,
)

_RATING_PATTERNS = [
    re.compile(r'[Оо]ценка[:\s]+(\d+(?:[.,]\d+)?)\s*\((\d+)\)'),
    re.compile(r'(\d+(?:[.,]\d+)?)\s*\*\s*(\d+)\s*(?:оцен|голос)', re.IGNORECASE),
    re.compile(r'<b>(\d+(?:[.,]\d+)?)</b>[^<]{0,30}?(\d+)\s*(?:оцен|голос)', re.IGNORECASE),
]


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

        method = sopds_cfg.SOPDS_SAMLIB_METHOD or 'series'
        self.stdout.write(f"Запуск fetch_samlib_ratings (метод: {method})…")

        while True:
            book = self._next_book()
            if book is None:
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                time.sleep(self._SLEEP_IDLE)
                continue

            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status = self._process_book(book, method)

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
        without = Book.objects.exclude(samlib_rating__isnull=False).first()
        if without:
            return without
        stale = Book.objects.filter(
            samlib_rating__fetched_at__lt=stale_cutoff
        ).first()
        return stale

    def _is_compilation(self, book):
        return bool(_COMPILATION_RE.search(book.title))

    def _process_book(self, book, method):
        if self._is_compilation(book):
            if method == 'fb2':
                rating, votes, url, error, individual = self._fetch_by_fb2(book)
            else:
                rating, votes, url, error, individual = self._fetch_by_series(book)
        else:
            author_name = ""
            first_author = book.authors.first()
            if first_author:
                author_name = first_author.full_name
            rating, votes, url, error = self._fetch_single_rating(author_name, book.title)
            individual = []

        if rating is not None:
            self.stdout.write(f"  Рейтинг: {rating} ({votes} голосов)")
        elif error:
            self.stdout.write("  Ошибка при получении рейтинга")
        else:
            self.stdout.write("  Рейтинг не найден")

        self._save_rating(book, rating, votes, url, error, individual)

        # Detect throttle HTTP status
        if error and url:
            return None
        return 200

    def _fetch_by_series(self, book):
        """Метод 'series': ищем по странице автора, находим серию, усредняем."""
        first_author = book.authors.first()
        if not first_author:
            return None, 0, '', False, []

        first_series = book.series.first()
        if not first_series:
            # Нет серии — обрабатываем как одиночную книгу
            rating, votes, url, error = self._fetch_single_rating(
                first_author.full_name, book.title
            )
            return rating, votes, url, error, []

        author_name = first_author.full_name
        series_name = first_series.ser

        # Шаг 1: ищем автора через POST (cp1251)
        seek_url = "http://samlib.ru/cgi-bin/seek"
        post_data = (
            "FIND=" + urllib.parse.quote(author_name.encode("cp1251")) + "&PLACE=index"
        ).encode("ascii")
        self.stdout.write(f"  POST {seek_url} FIND={author_name!r}")
        try:
            html, status = self._fetch(seek_url, post_data)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, 0, seek_url, True, []

        if status not in (None, 200):
            return None, 0, seek_url, True, []

        # Ищем ссылку вида /a/author_dir/ в HTML
        author_dir_re = re.compile(r'href=["\']/(a/[^/"\']+/)["\']', re.IGNORECASE)
        matches = author_dir_re.findall(html)
        if not matches:
            return None, 0, seek_url, False, []

        author_dir = matches[0]
        author_url = f"http://samlib.ru/{author_dir}"

        # Шаг 2: страница автора
        self.stdout.write(f"  GET {author_url}")
        try:
            author_html, status2 = self._fetch(author_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, 0, author_url, True, []

        if status2 not in (None, 200):
            return None, 0, author_url, True, []

        # Шаг 3: ищем секцию с названием серии
        # Ищем блок текста вокруг упоминания названия серии
        ser_idx = author_html.lower().find(series_name.lower())
        if ser_idx == -1:
            return None, 0, author_url, False, []

        # Берём фрагмент после заголовка серии (до следующего крупного блока)
        section_html = author_html[ser_idx:ser_idx + 20000]

        # Шаг 4: ищем рейтинги книг в секции
        individual = []
        rating_re = re.compile(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{1,200})</a>[^<]{0,500}?'
            r'(?:' + '|'.join(p.pattern for p in _RATING_PATTERNS) + r')',
            re.IGNORECASE | re.DOTALL,
        )

        # Простой подход: найти все числа вида X.XX рядом с количеством голосов
        book_rating_re = re.compile(
            r'(\d+\.\d+)\s*\*\s*(\d+)',
        )
        title_re = re.compile(r'<a[^>]+>([^<]{2,150})</a>', re.IGNORECASE)

        found_ratings = book_rating_re.findall(section_html)
        found_titles = title_re.findall(section_html)

        for i, (r_str, v_str) in enumerate(found_ratings):
            try:
                r = float(r_str)
                v = int(v_str)
            except ValueError:
                continue
            title = found_titles[i] if i < len(found_titles) else f"Книга {i+1}"
            individual.append({"title": title.strip(), "rating": r, "votes": v})

        return self._aggregate(individual, author_url)

    def _fetch_by_fb2(self, book):
        """Метод 'fb2': извлекаем заголовки из FB2 и ищем каждый."""
        from opds_catalog.models import Book as BookModel

        first_author = book.authors.first()
        author_name = first_author.full_name if first_author else ""

        # Определяем путь к файлу
        cat = book.catalog
        cat_path = cat.path if cat else ""
        filename = book.filename

        if cat_path:
            import os
            file_path = os.path.join(cat_path, filename)
        else:
            file_path = filename

        # Читаем FB2 (возможно, внутри ZIP)
        try:
            if file_path.lower().endswith('.fb2.zip') or file_path.lower().endswith('.zip'):
                with zipfile.ZipFile(file_path, 'r') as z:
                    names = [n for n in z.namelist() if n.lower().endswith('.fb2')]
                    if not names:
                        return None, 0, '', False, []
                    content = z.read(names[0])
            else:
                with open(file_path, 'rb') as f:
                    content = f.read()
        except Exception as exc:
            self.stdout.write(f"  Не удалось открыть файл: {exc}")
            return None, 0, '', False, []

        # Пробуем декодировать
        try:
            text = content.decode('utf-8', errors='replace')
        except Exception:
            try:
                text = content.decode('cp1251', errors='replace')
            except Exception:
                text = content.decode('latin-1', errors='replace')

        # Ищем заголовки секций
        section_title_re = re.compile(
            r'<section[^>]*>.*?<title[^>]*>(.*?)</title>',
            re.DOTALL | re.IGNORECASE,
        )
        tag_re = re.compile(r'<[^>]+>')

        titles = []
        for m in section_title_re.finditer(text):
            raw = m.group(1)
            clean = tag_re.sub('', raw).strip()
            if clean and len(clean) > 1:
                titles.append(clean)
            if len(titles) >= 20:
                break

        if not titles:
            return None, 0, '', False, []

        individual = []
        last_url = ''
        fetch_error = False

        for title in titles:
            r, v, url, err = self._fetch_single_rating(author_name, title)
            last_url = url or last_url
            if err:
                fetch_error = True
            if r is not None and r > 0:
                individual.append({"title": title, "rating": r, "votes": v})

        return self._aggregate(individual, last_url, fetch_error)

    def _fetch_single_rating(self, author_name, title):
        """Ищет рейтинг конкретной книги на samlib.ru."""
        query = f"{author_name} {title}".strip()
        url = "http://samlib.ru/cgi-bin/seek"
        post_data = (
            "FIND=" + urllib.parse.quote(query.encode("cp1251")) + "&PLACE=index"
        ).encode("ascii")
        self.stdout.write(f"  POST {url} FIND={query!r}")

        try:
            html, status = self._fetch(url, post_data)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, 0, url, True

        if status not in (None, 200):
            return None, 0, url, True

        rating, votes = self._parse_rating(html)
        return rating, votes, url, False

    def _aggregate(self, individual, url, fetch_error=False):
        """Усредняет individual_ratings, пропуская нулевые рейтинги."""
        valid = [x for x in individual if x.get('rating', 0) > 0]
        if not valid:
            return None, 0, url, fetch_error, individual

        total_votes = sum(x['votes'] for x in valid)
        if total_votes > 0:
            avg = sum(x['rating'] * x['votes'] for x in valid) / total_votes
        else:
            avg = sum(x['rating'] for x in valid) / len(valid)

        return round(avg, 2), total_votes, url, fetch_error, individual

    def _fetch(self, url, post_data=None):
        req = urllib.request.Request(url, data=post_data, headers={"User-Agent": self._UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                html = resp.read().decode("cp1251", errors="replace")
            return html, status
        except urllib.error.HTTPError as e:
            return "", e.code
        except Exception as exc:
            raise exc

    def _parse_rating(self, html):
        """Ищет паттерн рейтинга в HTML Самиздата."""
        for pat in _RATING_PATTERNS:
            m = pat.search(html)
            if m:
                try:
                    rating = float(m.group(1).replace(",", "."))
                    votes = int(m.group(2))
                    return rating, votes
                except (ValueError, IndexError):
                    continue
        return None, 0

    def _save_rating(self, book, rating, votes, url, fetch_error, individual):
        from opds_catalog.models import SamlibRating
        SamlibRating.objects.update_or_create(
            book=book,
            defaults={
                "rating": rating,
                "votes": votes,
                "samlib_url": url or '',
                "fetched_at": timezone.now(),
                "fetch_error": fetch_error,
                "individual_ratings": json.dumps(individual, ensure_ascii=False),
            },
        )
