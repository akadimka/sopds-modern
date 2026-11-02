"""Читалка: возврат к месту чтения, кнопки «к библиотеке» и «сохранить для
чтения без сети» (PWA, 2026-11-02).

Время сохранённой на сервере позиции уходит в страницу: место, сохранённое
на устройстве (читали без сети, открыта сохранённая копия), побеждает только
если новее. Место на устройстве — отдельно для каждого пользователя. Кнопка
«к библиотеке» — только когда её адрес задан (читалка внутри приложения,
/web/read/), адрес экранируется; «сохранить без сети» — там же и только для
вошедшего.
"""
import re

from convert.fb2_to_html import convert_bytes_to_html_string

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><book-title>Книга</book-title></title-info></description>
<body><section><p>Первый</p><p>Второй</p></section></body></FictionBook>
""".encode("utf-8")


def test_resume_time_and_anchor_are_in_the_page():
    html = convert_bytes_to_html_string(_FB2, progress_url="/opds/progress/7/", resume_anchor="ref-2",
                                        resume_time=1700000000123, user_id=42)
    assert "var RESUME_TIME = 1700000000123;" in html
    assert 'var RESUME_ANCHOR = "ref-2";' in html
    assert "var LOCAL_KEY = 'sopds_progress:' + 42 + ':' + PROGRESS_URL;" in html   # своё у каждого
    # позиция не сохраняется, пока страница не легла (картинки сдвигают текст)
    assert "window.addEventListener('load', settle)" in html and "if (settling) return;" in html


def test_back_button_only_inside_the_app():
    assert not re.search(r'<a [^>]*data-act="back"', convert_bytes_to_html_string(_FB2))
    html = convert_bytes_to_html_string(_FB2, back_url='/web/?a=1&b="2"')
    link = re.search(r'<a href="([^"]*)" data-act="back"', html)
    assert link and link.group(1) == "/web/?a=1&amp;b=&quot;2&quot;"


def test_offline_save_only_inside_the_app_for_a_signed_in_reader():
    def has_button(html):
        return re.search(r'<button [^>]*data-act="offline"', html) is not None

    assert not has_button(convert_bytes_to_html_string(_FB2, user_id=42))            # /opds/view/
    assert not has_button(convert_bytes_to_html_string(_FB2, back_url="/web/"))      # без входа
    html = convert_bytes_to_html_string(_FB2, back_url="/web/", user_id=42)
    assert has_button(html)
    assert "var USER = 42;" in html and "var CACHE = 'sopds-books';" in html
