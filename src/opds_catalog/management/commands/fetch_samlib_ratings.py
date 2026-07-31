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
from datetime import timedelta

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
    # Реальный формат samlib.ru: "Оценка:<b>5.74*16</b>" (страница автора) или
    # "Оценка: <b><a href=...>5.74*16</a></b>" (страница книги) — между
    # "Оценка" и числом может быть произвольное число HTML-тегов.
    re.compile(r'[Оо]ценк[аи][:\s]*(?:<[^>]+>\s*){0,5}(\d+(?:[.,]\d+)?)\s*\*\s*(\d+)', re.IGNORECASE),
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
        self.stdout.write("Запуск fetch_samlib_ratings…")

        from opds_catalog.ratings_progress import set_progress
        from opds_catalog.ratings_fetchers import sleep_or_stop, stop_requested, clear_stop

        _SRC = "samlib"

        while True:
            if stop_requested(_SRC):
                set_progress(_SRC, status="stopped")
                self.stdout.write("Остановлено по запросу.")
                clear_stop(_SRC)
                return

            if not sopds_cfg.SOPDS_SAMLIB_RATING:
                # Настройка выключена в Settings — НЕ завершаем процесс: под systemd
                # (см. is_systemd_managed() в ratings_fetchers.py) включение галочки
                # обратно не перезапускает юнит самостоятельно, только "будит" уже
                # работающий процесс через request_wake(). Если процесс завершится
                # здесь, никто больше не разбудит его без systemctl start вручную.
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_IDLE)
                set_progress(_SRC, status="disabled", resume_at=resume_at)
                sleep_or_stop(_SRC, self._SLEEP_IDLE)
                continue

            method = sopds_cfg.SOPDS_SAMLIB_METHOD or 'series'
            book = self._next_book()
            if book is None:
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_IDLE)
                set_progress("samlib", status="idle", resume_at=resume_at)
                self.stdout.write(
                    f"Все книги обработаны. Следующий цикл через {self._SLEEP_IDLE // 3600} ч."
                )
                sleep_or_stop(_SRC, self._SLEEP_IDLE)  # прерывается по stop_requested(), проверка в начале цикла
                continue

            set_progress("samlib", status="processing",
                         book_id=book.id, book_title=book.title)
            self.stdout.write(f"Обрабатываю: [{book.id}] {book.title}")
            status, result = self._process_book(book, method)

            if status in (429, 503):
                resume_at = timezone.now() + timedelta(seconds=self._SLEEP_THROTTLE)
                set_progress("samlib", status="throttled", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(
                    f"HTTP {status} — пауза {self._SLEEP_THROTTLE // 60} мин."
                )
                sleep_or_stop(_SRC, self._SLEEP_THROTTLE)
            else:
                delay = random.uniform(self._SLEEP_MIN, self._SLEEP_MAX)
                resume_at = timezone.now() + timedelta(seconds=delay)
                set_progress("samlib", status="sleeping", book_id=book.id,
                            book_title=book.title, last_result=result, resume_at=resume_at)
                self.stdout.write(f"  Пауза {delay:.0f} с.")
                sleep_or_stop(_SRC, delay)

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
            result = "found"
        elif error:
            self.stdout.write("  Ошибка при получении рейтинга")
            result = "error"
        else:
            self.stdout.write("  Рейтинг не найден")
            result = "not_found"

        self._save_rating(book, rating, votes, url, error, individual)

        # Detect throttle HTTP status
        if error and url:
            return None, result
        return 200, result

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

        author_html, author_url, error = self._find_author_page(author_name)
        if error:
            return None, 0, author_url, True, []
        if author_html is None:
            return None, 0, author_url, False, []

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
        """Метод 'fb2': извлекаем заголовки из FB2 и ищем каждый.

        Читаем через общий opds_catalog.utils.getFileData — тот же хелпер,
        которым пользуются Download/Cover/ViewHtml. Раньше здесь путь
        собирался вручную (Catalog.path + filename, без SOPDS_ROOT_LIB) и
        определение "это zip?" делалось по расширению итоговой строки —
        для CAT_ZIP/CAT_INP книг Catalog.path указывает на сам .zip-файл,
        а не на папку, так что filename дописывался ПОСЛЕ ".zip" и итоговый
        путь всегда оканчивался на ".fb2", а не на ".zip" — проверка никогда
        не срабатывала, и метод тихо считал, что раздел не найден, для
        КАЖДОЙ книги, хранящейся в .fb2.zip.
        """
        from opds_catalog.utils import getFileData

        first_author = book.authors.first()
        author_name = first_author.full_name if first_author else ""

        file_data = getFileData(book)
        if file_data is None:
            self.stdout.write("  Не удалось открыть файл")
            return None, 0, '', False, []
        content = file_data.read()

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

    def _find_author_page(self, author_name):
        """Ищет автора на samlib.ru, возвращает (html, url, error) его страницы.

        /cgi-bin/seek на практике находит что-то только по ОДНОМУ слову —
        составные запросы вроде "Фамилия Имя Отчество" или "Автор Название"
        не дают ни одного результата (проверено вживую: пустая форма и для
        "Сергей Лукьяненко", и для "Лукьяненко Сергей", и т.п.), поэтому
        ищем по фамилии (первое слово ФИО), а среди найденных ссылок на
        авторов берём ту, чей текст содержит полное имя.
        """
        surname = author_name.split()[0] if author_name else ''
        if not surname:
            return None, '', False

        seek_url = "http://samlib.ru/cgi-bin/seek"
        self.stdout.write(f"  GET {seek_url} FIND={surname!r}")
        try:
            html, status = self._seek(surname)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, seek_url, True

        if status not in (None, 200):
            return None, seek_url, True

        author_link_re = re.compile(
            r'href=["\']?(/[a-zа-я0-9]/[^/"\'>\s]+/)["\']?>\s*<font[^>]*>([^<]+)</font>',
            re.IGNORECASE,
        )
        author_dir = None
        for href, name_text in author_link_re.findall(html):
            if author_name.lower() in name_text.lower():
                author_dir = href
                break
        if author_dir is None:
            # Среди результатов поиска по фамилии нет автора с точно таким
            # именем (например, самого автора просто нет на samlib.ru) —
            # НЕ берём первую попавшуюся однобуквенную ссылку на странице:
            # ей может оказаться вообще не автор (например /i/info/), что
            # молча даст рейтинг совсем не того человека.
            return None, seek_url, False

        author_url = f"http://samlib.ru{author_dir}"
        self.stdout.write(f"  GET {author_url}")
        try:
            author_html, status2 = self._fetch(author_url)
        except Exception as exc:
            self.stdout.write(f"  Ошибка: {exc}")
            return None, author_url, True

        if status2 not in (None, 200):
            return None, author_url, True

        return author_html, author_url, False

    def _fetch_single_rating(self, author_name, title):
        """Ищет рейтинг конкретной книги на samlib.ru.

        Раньше искали по строке "автор название" целиком через /cgi-bin/seek
        и пытались распарсить рейтинг прямо со страницы результатов поиска —
        оба шага были нерабочими: составные запросы ничего не находят (см.
        _find_author_page), а страница результатов вообще не содержит
        рейтинг (там только размер файла вроде "155k"); оценка вида
        "Оценка:5.74*16" есть только на странице автора. Поэтому теперь идём
        через страницу автора и ищем название книги уже в её списке работ.
        """
        author_html, author_url, error = self._find_author_page(author_name)
        if error:
            return None, 0, author_url, True
        if author_html is None:
            return None, 0, author_url, False

        idx = author_html.lower().find(title.lower())
        if idx == -1:
            return None, 0, author_url, False

        section_html = author_html[idx:idx + 2000]
        rating, votes = self._parse_rating(section_html)
        return rating, votes, author_url, False

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

    def _seek(self, query):
        """Ищет `query` в поиске samlib.ru.

        /cgi-bin/seek принимает и POST, и GET, но на практике POST-запрос
        (как здесь и было раньше) отдаёт только пустую форму поиска — сервер
        видно принимает параметры формы только через query string. GET с теми
        же параметрами возвращает реальную страницу результатов (проверено:
        POST ~6 КБ пустой формы против GET ~48 КБ с результатами для того же
        запроса), поэтому ищем только через GET.
        """
        url = "http://samlib.ru/cgi-bin/seek?FIND=" + urllib.parse.quote(
            query.encode("cp1251")
        ) + "&PLACE=index&JANR=0&TYPE=0"
        return self._fetch(url)

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
