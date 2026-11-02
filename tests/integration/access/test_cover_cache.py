"""Кэш обложек (PWA, 2026-11-02): обложка и миниатюра отдаются с
`Cache-Control: private` и ETag — браузер держит их неделю и потом
переспрашивает (304 без извлечения из архива). Раньше заголовков не было:
каждый список книг заново качал все миниатюры. Проверка ETag — после
видимости: чужая книга по-прежнему 404."""
import pytest
from django.urls import reverse

from opds_catalog import dl

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ["opds:cover", "opds:thumb"])
def test_cover_is_cached_privately_and_revalidated(client, lib, name):
    users, books, _ = lib
    client.force_login(users["anna"])
    url = reverse(name, args=(books["anna"].pk,))
    first = client.get(url)
    assert first.status_code in (200, 302)          # 302 — «нет обложки»
    assert first["Cache-Control"] == f"private, max-age={dl.COVER_MAX_AGE}"
    etag = first["ETag"]
    again = client.get(url, HTTP_IF_NONE_MATCH=etag)
    assert again.status_code == 304 and again["ETag"] == etag and not again.content


def test_thumbnail_and_cover_have_different_etags(client, lib):
    users, books, _ = lib
    client.force_login(users["anna"])
    cover = client.get(reverse("opds:cover", args=(books["anna"].pk,)))["ETag"]
    thumb = client.get(reverse("opds:thumb", args=(books["anna"].pk,)))["ETag"]
    assert cover != thumb


def test_foreign_book_is_404_even_with_its_etag(client, lib):
    users, books, _ = lib
    client.force_login(users["boris"])
    etag = client.get(reverse("opds:thumb", args=(books["boris"].pk,)))["ETag"]
    client.force_login(users["anna"])
    response = client.get(reverse("opds:thumb", args=(books["boris"].pk,)), HTTP_IF_NONE_MATCH=etag)
    assert response.status_code == 404
