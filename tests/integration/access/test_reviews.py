"""Отзывы читателей о книгах (docs/reviews-design.md).

- отзыв к чужой книге не пишется и не виден (404, как несуществующая книга);
- подпись: свой — «ваш отзыв», суперпользователю — логин, остальным —
  «Читатель» или отображаемое имя, если автор разрешил; логин — никому, кроме
  суперпользователя;
- отзыв привязан к пути файла: переезжает с файлом, при сборке томов — на
  сборник с пометкой тома, при удалении файла прячется и возвращается с ним;
- суперпользователь скрывает отзыв; средняя оценка — в карточке, списках и
  описании книги в OPDS.
"""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from opds_catalog import ownership, reviews
from opds_catalog.models import Review
from opds_catalog.services import book_services
from sopds_web_backend.models import ActivityEvent, UserProfile

pytestmark = pytest.mark.django_db


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _review(client, book, rating, text=""):
    return client.post(reverse("web:review_save", args=[book.pk]), {"rating": rating, "text": text})


def test_review_of_a_foreign_book_is_not_written_or_shown(lib):
    users, books, _ = lib
    boris = _client(users["boris"])
    assert _review(boris, books["anna"], 5, "чужая").status_code == 404
    assert boris.post(reverse("web:review_delete", args=[books["anna"].pk]), {"review_id": 1}).status_code == 404
    assert not Review.objects.exists()
    _review(_client(users["anna"]), books["anna"], 4, "своя")
    assert boris.get(reverse("web:book_card", args=[books["anna"].pk])).status_code == 404


def test_one_review_per_book_rating_required_only_own_deleted(lib):
    users, books, _ = lib
    anna = _client(users["anna"])
    assert _review(anna, books["anna"], 0).status_code == 422
    _review(anna, books["anna"], 3, "Неплохо")
    page = _review(anna, books["anna"], 5).content.decode()          # только оценка, без текста
    [review] = Review.objects.all()
    assert (review.rating, review.text) == (5, "") and "your review" in page
    admin = _client(users["admin"])
    admin.post(reverse("web:review_delete", args=[books["anna"].pk]), {"review_id": review.pk})
    assert Review.objects.exists()                                    # чужой отзыв удалить нельзя
    anna.post(reverse("web:review_delete", args=[books["anna"].pk]), {"review_id": review.pk})
    assert not Review.objects.exists()
    assert [e.action for e in ActivityEvent.objects.filter(category="reading")] == [
        "review_deleted", "review_saved", "review_saved"]


def test_signature_hides_logins(lib):
    users, books, _ = lib
    anna = users["anna"]
    anna_book = books["anna"]
    UserProfile.objects.filter(user=users["boris"]).update(library_public=True)
    boris_book = books["boris"]
    _review(_client(anna), boris_book, 4, "<b>текст</b>")
    card = reverse("web:book_card", args=[boris_book.pk])
    seen_by_boris = _client(users["boris"]).get(card).content.decode()
    assert "Reader" in seen_by_boris and "anna" not in seen_by_boris
    assert "&lt;b&gt;текст&lt;/b&gt;" in seen_by_boris                # текст отзыва — экранирован
    assert "anna" in _client(users["admin"]).get(card).content.decode()
    profile = reverse("web:profile")
    a = _client(anna)
    assert "display name first" in a.post(profile, {"action": "sign_reviews", "sign_reviews": "on"}).content.decode()
    UserProfile.objects.filter(user=anna).update(display_name="Аня из Петербурга")
    a.post(profile, {"action": "sign_reviews", "sign_reviews": "on"})
    assert "Аня из Петербурга" in _client(users["boris"]).get(card).content.decode()
    assert anna_book.pk  # книга Анны — для профиля ниже
    assert boris_book.display_title in a.get(profile).content.decode()  # «Мои отзывы»


def test_review_follows_the_file(lib, tmp_path):
    users, books, _ = lib
    book = books["anna"]
    _review(_client(users["anna"]), book, 4)
    old = ownership.book_rel_path(book)
    ownership.transfer(old, "Фантастика/Автор Анны/Автор Анны - Дневник Анны.fb2.zip")
    assert Review.objects.get().rel_path == "Фантастика/Автор Анны/Автор Анны - Дневник Анны.fb2.zip"
    ownership.merge(["Фантастика/Автор Анны/Автор Анны - Дневник Анны.fb2.zip"],
                    "Фантастика/Автор Анны/Автор Анны - Дневники (Дилогия).fb2")
    review = Review.objects.get()
    assert (review.rel_path, review.about) == ("Фантастика/Автор Анны/Автор Анны - Дневники (Дилогия).fb2",
                                               "Автор Анны - Дневник Анны")
    ownership.release(review.rel_path, file_exists=False)
    assert reviews.rating_for_rel(review.rel_path) is None              # файла нет — спрятан, не удалён
    ownership.assign(review.rel_path, users["anna"])
    assert reviews.rating_for_rel(review.rel_path).count == 1           # файл вернулся — и отзыв


def test_superuser_hides_review_and_rating_shows_everywhere(lib):
    users, books, _ = lib
    book = books["anna"]
    _review(_client(users["anna"]), book, 4)
    UserProfile.objects.filter(user=users["anna"]).update(library_public=True)
    _review(_client(users["boris"]), book, 5)
    rating = reviews.rating_for_book(book)
    assert (rating.average, rating.count, rating.stars) == (4.5, 2, "★★★★☆")
    item = {"reader_rating": rating, "title": book.title, "authors": [], "genres": [], "series": [], "ser_no": [],
            "filename": book.filename, "filesize": 0, "docdate": "", "annotation": ""}
    assert "★★★★☆ 4.5 (2)" in book_services.book_description(item)
    boris_review = Review.objects.get(user=users["boris"])
    assert _client(users["anna"]).post(reverse("web:review_moderate", args=[boris_review.pk, "hide"])).status_code \
        in (302, 403)
    assert not Review.objects.get(pk=boris_review.pk).hidden
    _client(users["admin"]).post(reverse("web:review_moderate", args=[boris_review.pk, "hide"]))
    assert reviews.rating_for_book(book).count == 1
    tab = _client(users["admin"]).get(reverse("web:users_list"), {"tab": "reviews"}).content.decode()
    assert "boris" in tab and "Show again" in tab
    listing = _client(users["anna"]).get(reverse("web:searchbooks"), {"searchtype": "m", "searchterms": "Дневник"})
    assert "♥ 4.0 (1)" in listing.content.decode()


def test_user_deleted_reviews_go_with_the_account(lib):
    users, books, _ = lib
    _review(_client(users["anna"]), books["anna"], 2)
    User.objects.filter(pk=users["anna"].pk).delete()
    assert not Review.objects.exists()
