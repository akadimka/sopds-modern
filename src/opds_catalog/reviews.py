"""Отзывы читателей о книгах (docs/reviews-design.md).

Отзыв привязан к пути файла в библиотеке (`ownership.book_rel_path`), а не к
записи `Book`: она пересоздаётся при перемещении, сжатии и сборке томов.
Переезд, слияние и удаление файла — те же события, что у владения
(`opds_catalog/ownership.py` вызывает `on_transfer`/`on_merge`/`on_release`).

Видит отзывы тот, кто видит книгу: доступ к книге проверяет вызывающий
(`visibility.get_visible_book_or_404`). Подпись чужого отзыва — «Читатель»
или отображаемое имя, если автор разрешил в профиле; логин — только
суперпользователю (CLAUDE.md, имена других пользователей).
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Iterable, Optional

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from opds_catalog.models import Review, book_display_name


@dataclass(frozen=True)
class Rating:
    average: float
    count: int

    @property
    def stars(self) -> str:
        full = int(round(self.average))
        return "★" * full + "☆" * (5 - full)


def _rel(book) -> str:
    from opds_catalog.ownership import book_rel_path
    return book_rel_path(book)


def _shown(qs):
    return qs.filter(hidden=False, file_missing=False)


def rating_for_rel(rel: str) -> Optional[Rating]:
    row = _shown(Review.objects.filter(rel_path=rel)).aggregate(avg=Avg("rating"), n=Count("id"))
    return Rating(round(row["avg"], 1), row["n"]) if row["n"] else None


def rating_for_book(book) -> Optional[Rating]:
    return rating_for_rel(_rel(book))


def ratings_for_rels(rels: Iterable[str]) -> dict:
    """Средние оценки сразу для страницы книг: {путь: Rating}."""
    rels = set(rels)
    if not rels:
        return {}
    rows = (_shown(Review.objects.filter(rel_path__in=rels)).values("rel_path")
            .annotate(avg=Avg("rating"), n=Count("id")))
    return {r["rel_path"]: Rating(round(r["avg"], 1), r["n"]) for r in rows}


def attach_ratings(items: list, rows: list) -> None:
    """Средняя оценка читателей в словари книг страницы — одним запросом на
    страницу (`items[i]` построен из `rows[i]`)."""
    from opds_catalog.ownership import book_rel_path
    rels = [book_rel_path(row) for row in rows]
    found = ratings_for_rels(rels)
    for item, rel in zip(items, rels):
        item["reader_rating"] = found.get(rel)


def signature(review, viewer) -> str:
    """Подпись отзыва для этого зрителя: свой — «ваш отзыв»,
    суперпользователю — логин, остальным — отображаемое имя автора, если он
    разрешил подпись, иначе «Читатель»."""
    from django.utils.translation import gettext as _
    if viewer is not None and viewer.is_authenticated:
        if viewer.pk == review.user_id:
            return _("your review")
        if viewer.is_superuser:
            return review.user.username
    profile = getattr(review.user, "profile", None)
    if profile is not None and profile.sign_reviews and profile.display_name.strip():
        return profile.display_name.strip()
    return _("Reader")


def book_reviews(book, viewer) -> list:
    """Отзывы к книге для показа: не скрытые; свои — первыми."""
    reviews = list(_shown(Review.objects.filter(rel_path=_rel(book))).select_related("user", "user__profile"))
    for r in reviews:
        r.signature = signature(r, viewer)
        r.own = viewer is not None and viewer.pk == r.user_id
        r.stars = "★" * r.rating + "☆" * (5 - r.rating)
    reviews.sort(key=lambda r: (not r.own, -r.updated.timestamp()))
    return reviews


def own_review(book, user) -> Optional[Review]:
    """Отзыв пользователя на саму книгу (не пометку «о томе»)."""
    return Review.objects.filter(rel_path=_rel(book), user=user, about="").first()


def clean_rating(raw) -> Optional[int]:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 5 else None


def save(book, user, rating: int, text: str) -> Review:
    """Создать или изменить свой отзыв на книгу."""
    text = (text or "").replace("\r\n", "\n").strip()[:Review.MAX_TEXT]
    review, created = Review.objects.get_or_create(
        rel_path=_rel(book), user=user, about="", defaults={"rating": rating, "text": text})
    if not created:
        review.rating, review.text, review.updated = rating, text, timezone.now()
        review.save(update_fields=["rating", "text", "updated"])
    return review


# ── События файла (вызывает opds_catalog/ownership.py) ───────────────────────

def _keep_newer(review, rel, about):
    """На новом месте у пользователя уже есть отзыв с той же пометкой —
    остаётся более свежий."""
    other = Review.objects.filter(rel_path=rel, user_id=review.user_id, about=about).exclude(pk=review.pk).first()
    if other is None:
        return True
    if other.updated >= review.updated:
        review.delete()
        return False
    other.delete()
    return True


def on_transfer(old_rel: str, new_rel: str) -> None:
    """Файл переехал (перемещение, переименование, сжатие) — отзывы с ним."""
    with transaction.atomic():
        for review in list(Review.objects.filter(rel_path=old_rel)):
            if _keep_newer(review, new_rel, review.about):
                review.rel_path, review.file_missing = new_rel, False
                review.save(update_fields=["rel_path", "file_missing"])


def on_merge(sources: Iterable[str], target: str) -> None:
    """Тома слились в сборник: отзывы томов — на сборник с пометкой тома
    (решение пользователя: «о томе N «Название»»)."""
    with transaction.atomic():
        for source in sources:
            label = book_display_name(posixpath.basename(source))
            for review in list(Review.objects.filter(rel_path=source)):
                about = review.about or label
                if _keep_newer(review, target, about):
                    review.rel_path, review.about, review.file_missing = target, about[:300], False
                    review.save(update_fields=["rel_path", "about", "file_missing"])


def on_release(rel: str) -> None:
    """Файла больше нет: отзывы не удаляются, а прячутся до его возвращения."""
    Review.objects.filter(rel_path=rel, file_missing=False).update(file_missing=True)


def on_present(rel: str) -> None:
    """Файл снова в библиотеке (новая запись каталога, файл положили) —
    спрятанные из-за его отсутствия отзывы возвращаются."""
    Review.objects.filter(rel_path=rel, file_missing=True).update(file_missing=False)
