"""Читалка (/opds/view/<id>/) открыта на домене приложения, а FB2 — из
недоверенного источника: ссылка `l:href="javascript:…"` в книге исполнялась
бы по клику с правами текущей сессии. Во внешних ссылках разрешены только
http(s) и mailto, сноски/якоря (`#…`) — как раньше.
"""
import pytest

from convert.fb2_to_html import convert_bytes_to_html_string

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><book-title>Книга</book-title></title-info></description>
<body><section><p><a l:href="{href}">ссылка</a></p></section></body></FictionBook>
"""


def _render(href: str) -> str:
    return convert_bytes_to_html_string(_FB2.format(href=href).encode("utf-8"))


@pytest.mark.parametrize("href", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
])
def test_dangerous_scheme_rendered_as_text(href):
    html = _render(href.replace("<", "&lt;").replace(">", "&gt;"))
    assert "ссылка" in html
    assert 'href="javascript' not in html.lower()
    assert 'href=" javascript' not in html.lower()
    assert 'href="data:' not in html
    assert 'href="vbscript' not in html


@pytest.mark.parametrize("href", ["https://example.org/a?b=1", "http://example.org", "mailto:a@b.c"])
def test_safe_scheme_kept(href):
    assert 'href="' + href.replace("&", "&amp;") + '"' in _render(href)


def test_internal_anchor_kept():
    assert 'href="#sec1"' in _render("#sec1")
