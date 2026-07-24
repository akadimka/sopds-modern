#!/usr/bin/env python3
"""
FB2 → HTML converter.
Ported from ichbinkirgiz/sopds (June 2026).
Stdlib only — no external dependencies required.
"""
import argparse
import html
import json as _json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

FB_NS = "http://www.gribuser.ru/xml/fictionbook/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"fb": FB_NS, "xlink": XLINK_NS}


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def read_fb2(path: Path) -> bytes:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fb2")]
            if not names:
                raise ValueError("No .fb2 file found in ZIP.")
            return z.read(names[0])
    return path.read_bytes()


def read_fb2_bytes(data: bytes) -> bytes:
    """Accept raw bytes (for in-memory use from Django view)."""
    return data


def plain_text(elem) -> str:
    return " ".join("".join(elem.itertext()).split()) if elem is not None else ""


def xml_id(elem) -> str:
    return elem.attrib.get(f"{{{XML_NS}}}id", "") or elem.attrib.get("id", "")


def extract_binaries(root):
    binaries = {}
    for b in root.findall(".//fb:binary", NS):
        img_id = b.attrib.get("id")
        content_type = b.attrib.get("content-type", "application/octet-stream")
        data = re.sub(r"\s+", "", b.text or "")
        if img_id and data:
            binaries[img_id] = f"data:{content_type};base64,{data}"
    return binaries


def extract_description(root):
    title_info = root.find(".//fb:description/fb:title-info", NS)
    doc_info = root.find(".//fb:description/fb:document-info", NS)

    def get(path, base=title_info):
        node = base.find(path, NS) if base is not None else None
        return plain_text(node)

    authors = []
    if title_info is not None:
        for a in title_info.findall("fb:author", NS):
            first = plain_text(a.find("fb:first-name", NS))
            middle = plain_text(a.find("fb:middle-name", NS))
            last = plain_text(a.find("fb:last-name", NS))
            nickname = plain_text(a.find("fb:nickname", NS))
            name = " ".join(x for x in [first, middle, last] if x).strip()
            if not name:
                name = nickname
            if name:
                authors.append(name)

    genres = []
    if title_info is not None:
        genres = [plain_text(g) for g in title_info.findall("fb:genre", NS)]

    meta = {
        "Название": get("fb:book-title"),
        "Автор": ", ".join(authors),
        "Жанр": ", ".join(g for g in genres if g),
        "Язык": get("fb:lang"),
        "Дата": get("fb:date"),
        "Серия": "",
        "ID": "",
        "Версия": "",
    }

    sequence = title_info.find("fb:sequence", NS) if title_info is not None else None
    if sequence is not None:
        name = sequence.attrib.get("name", "")
        number = sequence.attrib.get("number", "")
        if name and number:
            meta["Серия"] = f"{name} #{number}"
        elif name:
            meta["Серия"] = name

    if doc_info is not None:
        meta["ID"] = plain_text(doc_info.find("fb:id", NS))
        meta["Версия"] = plain_text(doc_info.find("fb:version", NS))

    annotation = title_info.find("fb:annotation", NS) if title_info is not None else None
    coverpage = title_info.find("fb:coverpage", NS) if title_info is not None else None

    return meta, annotation, coverpage


def collect_note_ids(root):
    """Собрать все id секций из body[@name='notes'] — они являются сносками."""
    note_ids = set()
    for body in root.findall(".//fb:body[@name='notes']", NS):
        for sec in body.iter():
            sec_id = xml_id(sec)
            if sec_id:
                note_ids.add(sec_id)
    return note_ids


def render_inline(elem, binaries, note_ids=None, note_backrefs=None, current_anchor=None):
    tag = local_name(elem.tag)
    text = html.escape(elem.text or "")
    children = "".join(
        render_inline(child, binaries, note_ids, note_backrefs, current_anchor)
        for child in elem
    )
    tail = html.escape(elem.tail or "")

    if tag == "strong":
        return f"<strong>{text}{children}</strong>{tail}"
    if tag == "emphasis":
        return f"<em>{text}{children}</em>{tail}"
    if tag == "strikethrough":
        return f"<s>{text}{children}</s>{tail}"
    if tag == "sub":
        return f"<sub>{text}{children}</sub>{tail}"
    if tag == "sup":
        return f"<sup>{text}{children}</sup>{tail}"
    if tag == "code":
        return f"<code>{text}{children}</code>{tail}"
    if tag == "a":
        href = elem.attrib.get(f"{{{XLINK_NS}}}href", "#")
        if href.startswith("#"):
            target_id = href[1:]
            if note_ids and target_id in note_ids:
                # Это ссылка на сноску — добавляем обратную ссылку
                note_target = "note-" + target_id
                if note_backrefs is not None and current_anchor:
                    note_backrefs.setdefault(target_id, [])
                    if current_anchor not in note_backrefs[target_id]:
                        note_backrefs[target_id].append(current_anchor)
                return (
                    f'<a href="#{html.escape(note_target)}" class="note-ref">'
                    f"{text}{children}</a>{tail}"
                )
            # Обычный якорь внутри документа — оставляем как есть
            return f'<a href="{html.escape(href)}">{text}{children}</a>{tail}'
        return f'<a href="{html.escape(href)}">{text}{children}</a>{tail}'
    if tag == "image":
        href = elem.attrib.get(f"{{{XLINK_NS}}}href", "")
        img_id = href.lstrip("#")
        src = binaries.get(img_id, img_id)
        return f'<img src="{html.escape(src)}" alt="{html.escape(img_id)}" />{tail}'

    return f"{text}{children}{tail}"


def render_block(elem, binaries, heading_level=2, note_ids=None, note_backrefs=None,
                 in_notes=False, anchor_counter=None):
    tag = local_name(elem.tag)
    elem_id = xml_id(elem)

    if tag == "section":
        if in_notes and elem_id:
            html_id = "note-" + elem_id
        else:
            html_id = elem_id
        id_attr = f' id="{html.escape(html_id)}"' if html_id else ""

        # Секция "Содержание"/"Оглавление" (список ссылок на главы/тома) —
        # такая секция уже присутствует как обычный контент во многих
        # компиляциях. Помечаем классом, чтобы центрировать её как
        # book-header, а не растягивать на всю ширину как обычный текст.
        class_attr = ""
        if not in_notes:
            _title_elem = elem.find("fb:title", NS)
            _title_text = plain_text(_title_elem).strip().lower().replace("ё", "е") \
                if _title_elem is not None else ""
            if _title_text in ("содержание", "оглавление"):
                class_attr = ' class="toc"'

        back_link = ""
        note_number = ""
        if in_notes:
            title_elem = elem.find("fb:title", NS)
            if title_elem is not None:
                note_number = plain_text(title_elem)
        if in_notes and elem_id:
            refs = note_backrefs.get(elem_id, []) if note_backrefs else []
            if refs:
                links = " ".join(
                    f'<a href="#{html.escape(ref)}" class="note-backref">'
                    f'<span class="note-number">{html.escape(note_number)}</span> ↩</a>'
                    for ref in refs
                )
                back_link = f'<p class="note-backlinks">{links}</p>\n'
        children = []
        for child in elem:
            if in_notes and local_name(child.tag) == "title":
                continue
            children.append(
                render_block(child, binaries, heading_level + 1,
                             note_ids, note_backrefs, in_notes, anchor_counter)
            )
        return (
            f"<section{id_attr}{class_attr}>\n" + back_link + "".join(children) + "</section>\n"
        )

    html_id = elem_id
    id_attr = f' id="{html.escape(html_id)}"' if html_id else ""

    if tag == "title":
        text = plain_text(elem)
        level = min(heading_level, 6)
        return f"<h{level}>{html.escape(text)}</h{level}>\n" if text else ""
    if tag == "subtitle":
        return f"<h4>{render_inline(elem, binaries, note_ids, note_backrefs, html_id)}</h4>\n"
    if tag == "p":
        if not html_id and not in_notes:
            if anchor_counter is not None:
                anchor_counter["value"] += 1
                html_id = f"ref-{anchor_counter['value']}"
            else:
                html_id = f"ref-{id(elem)}"
            id_attr = f' id="{html.escape(html_id)}"'
        return (
            f"<p{id_attr}>"
            f"{render_inline(elem, binaries, note_ids, note_backrefs, html_id)}"
            f"</p>\n"
        )
    if tag == "empty-line":
        return "<br />\n"
    if tag == "epigraph":
        return (
            "<blockquote>\n"
            + "".join(
                render_block(child, binaries, heading_level, note_ids, note_backrefs,
                             in_notes, anchor_counter)
                for child in elem
            )
            + "</blockquote>\n"
        )
    if tag == "cite":
        return (
            '<blockquote class="cite">\n'
            + "".join(
                render_block(child, binaries, heading_level, note_ids, note_backrefs,
                             in_notes, anchor_counter)
                for child in elem
            )
            + "</blockquote>\n"
        )
    if tag == "poem":
        lines = []
        for stanza in elem:
            for v in stanza:
                if local_name(v.tag) == "v":
                    lines.append(plain_text(v))
            lines.append("")
        return '<pre class="poem">' + html.escape("\n".join(lines).strip()) + "</pre>\n"
    if tag == "image":
        return render_inline(elem, binaries, note_ids, note_backrefs, html_id) + "\n"

    return "".join(
        render_block(child, binaries, heading_level, note_ids, note_backrefs,
                     in_notes, anchor_counter)
        for child in elem
    )


def render_description(meta, annotation, binaries):
    rows = []
    for key, value in meta.items():
        if value:
            rows.append(
                f"<tr><th>{html.escape(key)}</th>"
                f"<td>{html.escape(value)}</td></tr>"
            )
    result = '<section class="description">\n<h2>Метаданные</h2>\n'
    if rows:
        result += "<table>\n" + "\n".join(rows) + "\n</table>\n"
    if annotation is not None:
        result += "<h3>Аннотация</h3>\n"
        for child in annotation:
            result += render_block(child, binaries, 4)
    result += "</section>\n"
    return result


def render_notes(root, binaries, note_ids, note_backrefs):
    notes_bodies = root.findall(".//fb:body[@name='notes']", NS)
    if not notes_bodies:
        return ""
    html_parts = ['<section class="notes">\n<h2>Примечания</h2>\n']
    for body in notes_bodies:
        for child in body:
            html_parts.append(
                render_block(child, binaries, 3, note_ids, note_backrefs, in_notes=True)
            )
    html_parts.append("</section>\n")
    return "".join(html_parts)


def render_coverpage(coverpage, binaries):
    if coverpage is None:
        return ""
    html_parts = ['<section class="coverpage">\n']
    for child in coverpage:
        if local_name(child.tag) == "image":
            html_parts.append(render_inline(child, binaries))
    html_parts.append("</section>\n")
    return "".join(html_parts)


_READER_CSS = """
:root {
    --reader-bg: #1b1b1b;
    --reader-fg: #ddd;
    --reader-link: #8ecfff;
    --reader-border: #444;
    --reader-toolbar-bg: rgba(30, 30, 30, .92);
    --reader-font-size: 19px;
    --reader-line-height: 1.6;
    --reader-align: left;
    --reader-max-width: none;
}
:root[data-theme="light"] {
    --reader-bg: #f4f1e8;
    --reader-fg: #262220;
    --reader-link: #1a5490;
    --reader-border: #ccc;
    --reader-toolbar-bg: rgba(244, 241, 232, .95);
}
body {
    margin: 3rem auto;
    padding: 0 3rem;
    font-family: Georgia, serif;
    font-size: var(--reader-font-size);
    line-height: var(--reader-line-height);
    color: var(--reader-fg);
    background-color: var(--reader-bg);
}
a { color: var(--reader-link); }
table { border-collapse: collapse; margin-bottom: 2rem; }
th { text-align: left; padding-right: 1rem; vertical-align: top; }
td, th { border-bottom: 1px solid var(--reader-border); padding: .35rem .75rem .35rem 0; }
img { max-width: 100%; display: block; margin: 1.5rem auto; }
blockquote {
    margin-left: 1.5rem; padding-left: 1rem;
    border-left: 3px solid var(--reader-border);
}
pre.poem { font-family: Georgia, serif; white-space: pre-wrap; }
.note-ref { text-decoration: none; vertical-align: super; font-size: .85em; }
.note-backlinks { font-size: .9em; margin-bottom: .5rem; }
.note-backref { text-decoration: none; }
.notes { margin-top: 4rem; border-top: 1px solid var(--reader-border); }
.book-header { max-width: 820px; margin: 0 auto; }
.coverpage { text-align: center; margin-bottom: 2rem; }
.coverpage img { max-width: 100%; max-height: 80vh; }
.note-number { font-weight: bold; font-size: 1.1em; margin-right: .3em; }
.toc {
    max-width: 820px; margin: 2rem auto 3rem; padding: 1rem;
    border: 1px solid var(--reader-border); text-align: center;
}
.toc ul { list-style: none; padding-left: 0; }
.toc li { margin: .25rem 0; }
.toc a { text-decoration: none; }
.toc-level-1 { font-weight: bold; }
.toc-level-2 { margin-left: 1.5rem; }
.toc-level-3 { margin-left: 3rem; }
.toc-level-4 { margin-left: 4.5rem; }

main { max-width: var(--reader-max-width); margin: 0 auto; }
main p { text-align: var(--reader-align); }

#reader-toolbar {
    position: fixed; top: 0; left: 50%;
    transform: translate(-50%, -120%);
    display: flex; align-items: center; gap: .3rem;
    background: var(--reader-toolbar-bg);
    border: 1px solid var(--reader-border);
    border-top: none;
    border-radius: 0 0 10px 10px;
    padding: .4rem .6rem;
    z-index: 1000;
    transition: transform .2s ease;
    box-shadow: 0 4px 14px rgba(0, 0, 0, .35);
}
#reader-toolbar.visible { transform: translate(-50%, 0); }
#reader-toolbar button {
    background: transparent; color: var(--reader-fg);
    border: 1px solid var(--reader-border); border-radius: 5px;
    padding: .3rem .55rem; font-size: .9rem; line-height: 1;
    font-family: Georgia, serif; cursor: pointer;
}
#reader-toolbar button:hover { background: var(--reader-border); }
#reader-toolbar button.active {
    background: var(--reader-link); color: var(--reader-bg);
    border-color: var(--reader-link);
}
#reader-toolbar .sep { width: 1px; height: 1.4rem; background: var(--reader-border); margin: 0 .15rem; }
"""

_READER_TOOLBAR_HTML = """
<div id="reader-toolbar">
  <button type="button" data-act="font-dec" title="Уменьшить шрифт">A−</button>
  <button type="button" data-act="font-inc" title="Увеличить шрифт">A+</button>
  <span class="sep"></span>
  <button type="button" data-act="lh-dec" title="Меньше межстрочный интервал">↕−</button>
  <button type="button" data-act="lh-inc" title="Больше межстрочный интервал">↕+</button>
  <span class="sep"></span>
  <button type="button" data-align="left" title="По левому краю">⯇</button>
  <button type="button" data-align="justify" title="По ширине">≡</button>
  <button type="button" data-align="right" title="По правому краю">⯈</button>
  <span class="sep"></span>
  <button type="button" data-act="width" title="Ширина колонки">⇔</button>
  <button type="button" data-act="theme" title="Светлая/тёмная тема">◐</button>
</div>
"""

_READER_TOOLBAR_JS = """
(function () {
    var KEY = 'sopds_reader_prefs';
    var defaults = { fontSize: 19, theme: 'dark', align: 'left', lineHeight: 1.6, width: 'wide' };
    var prefs;
    try {
        prefs = Object.assign({}, defaults, JSON.parse(localStorage.getItem(KEY) || '{}'));
    } catch (e) {
        prefs = Object.assign({}, defaults);
    }

    function widthValue() {
        if (prefs.width === 'narrow') return '650px';
        if (prefs.width === 'normal') return '900px';
        return 'none';
    }

    function apply() {
        var root = document.documentElement;
        root.style.setProperty('--reader-font-size', prefs.fontSize + 'px');
        root.style.setProperty('--reader-line-height', prefs.lineHeight);
        root.style.setProperty('--reader-align', prefs.align);
        root.style.setProperty('--reader-max-width', widthValue());
        root.setAttribute('data-theme', prefs.theme);
    }

    function save() {
        try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) {}
        apply();
    }

    apply();

    document.addEventListener('DOMContentLoaded', function () {
        var bar = document.getElementById('reader-toolbar');
        if (!bar) return;

        function byAct(act) { return bar.querySelector('[data-act="' + act + '"]'); }

        byAct('font-dec').onclick = function () { prefs.fontSize = Math.max(12, prefs.fontSize - 1); save(); };
        byAct('font-inc').onclick = function () { prefs.fontSize = Math.min(32, prefs.fontSize + 1); save(); };
        byAct('lh-dec').onclick = function () { prefs.lineHeight = Math.max(1.2, +(prefs.lineHeight - 0.1).toFixed(1)); save(); };
        byAct('lh-inc').onclick = function () { prefs.lineHeight = Math.min(2.2, +(prefs.lineHeight + 0.1).toFixed(1)); save(); };
        byAct('theme').onclick = function () { prefs.theme = prefs.theme === 'dark' ? 'light' : 'dark'; save(); };
        byAct('width').onclick = function () {
            prefs.width = prefs.width === 'wide' ? 'normal' : (prefs.width === 'normal' ? 'narrow' : 'wide');
            save();
        };

        var alignBtns = bar.querySelectorAll('[data-align]');
        alignBtns.forEach(function (btn) {
            btn.onclick = function () { prefs.align = btn.getAttribute('data-align'); save(); updateActive(); };
        });

        function updateActive() {
            alignBtns.forEach(function (b) {
                b.classList.toggle('active', b.getAttribute('data-align') === prefs.align);
            });
        }
        updateActive();

        // Показываем панель при скролле вверх (или у самого верха страницы),
        // прячем при скролле вниз — т.е. пока пользователь читает дальше, панель не мешает.
        var lastY = window.scrollY;
        var ticking = false;
        window.addEventListener('scroll', function () {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(function () {
                var y = window.scrollY;
                if (y < 40 || y < lastY - 5) {
                    bar.classList.add('visible');
                } else if (y > lastY + 5) {
                    bar.classList.remove('visible');
                }
                lastY = y;
                ticking = false;
            });
        }, { passive: true });
    });
})();
"""


def _build_progress_tracker_js(progress_url: str, resume_anchor: str) -> str:
    """JS: resume at the saved position on load, report reading progress on scroll.

    "Page" doesn't exist as a real concept in this continuous HTML (FB2 has no
    fixed pagination) — instead each paragraph already carries a document-order
    "ref-N" id (assigned once per whole document, across every <main> — see
    _build_html), so the paragraph's position among all of them doubles as an
    approximate page number, and its id is what a saved reading position points
    back to.
    """
    return f"""
(function () {{
    var PROGRESS_URL = {_json.dumps(progress_url)};
    var RESUME_ANCHOR = {_json.dumps(resume_anchor)};

    document.addEventListener('DOMContentLoaded', function () {{
        var paras = Array.prototype.slice.call(document.querySelectorAll('[id^="ref-"]'));

        if (RESUME_ANCHOR) {{
            var target = document.getElementById(RESUME_ANCHOR);
            if (target) target.scrollIntoView({{ block: 'start' }});
        }}

        if (!PROGRESS_URL || paras.length === 0) return;

        // Бинарный поиск последнего абзаца, чей верх уже проскроллен —
        // дёшево даже для книг с десятками тысяч абзацев.
        function currentIndex() {{
            var y = window.scrollY + 80;
            var lo = 0, hi = paras.length - 1, ans = 0;
            while (lo <= hi) {{
                var mid = (lo + hi) >> 1;
                if (paras[mid].offsetTop <= y) {{ ans = mid; lo = mid + 1; }} else {{ hi = mid - 1; }}
            }}
            return ans;
        }}

        var lastSentIndex = -1;
        function send(anchorId, unit, total, percent, useBeacon) {{
            var body = JSON.stringify({{
                anchor_id: anchorId, percent: percent,
                current_unit: unit, total_units: total,
            }});
            if (useBeacon && navigator.sendBeacon) {{
                navigator.sendBeacon(PROGRESS_URL, new Blob([body], {{ type: 'application/json' }}));
                return;
            }}
            fetch(PROGRESS_URL, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                credentials: 'same-origin',
                body: body,
                keepalive: true,
            }}).catch(function () {{}});
        }}

        function reportIfChanged(useBeacon) {{
            var idx = currentIndex();
            if (idx === lastSentIndex) return;
            lastSentIndex = idx;
            send(paras[idx].id, idx + 1, paras.length, ((idx + 1) / paras.length) * 100, useBeacon);
        }}

        var debounceTimer = null;
        window.addEventListener('scroll', function () {{
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(function () {{ reportIfChanged(false); }}, 1500);
        }}, {{ passive: true }});

        // На случай если пользователь закрывает вкладку раньше, чем сработает
        // debounce — sendBeacon гарантированно долетает даже при выгрузке страницы.
        window.addEventListener('pagehide', function () {{
            if (debounceTimer) clearTimeout(debounceTimer);
            lastSentIndex = -1; // форсируем отправку, даже если позиция не менялась с прошлого report
            reportIfChanged(true);
        }});
    }});
}})();
"""


def convert_bytes_to_html_string(fb2_data: bytes, progress_url: str = "", resume_anchor: str = "") -> str:
    """Convert raw FB2 bytes to an HTML string (for in-memory use in Django views).

    Args:
        progress_url: if given, the reader page POSTs {anchor_id, percent} to this
            URL as the user scrolls, so their reading position survives a reload.
            Empty string disables progress tracking (e.g. anonymous user).
        resume_anchor: id of the paragraph/section to scroll to on load (the last
            position saved for this user+book), or "" to start at the top.
    """
    root = ET.fromstring(fb2_data)
    return _build_html(root, title_hint="", progress_url=progress_url, resume_anchor=resume_anchor)


def fb2_to_html(input_path: Path, output_path: Path):
    """Convert an FB2/FB2.ZIP file on disk to an HTML file on disk."""
    root = ET.fromstring(read_fb2(input_path))
    html_doc = _build_html(root, title_hint=input_path.stem)
    output_path.write_text(html_doc, encoding="utf-8")


def _build_html(root, title_hint: str, progress_url: str = "", resume_anchor: str = "") -> str:
    binaries = extract_binaries(root)
    meta, annotation, coverpage = extract_description(root)
    title = meta.get("Название") or title_hint

    note_ids = collect_note_ids(root)
    note_backrefs: dict = {}
    anchor_counter = {"value": 0}

    normal_bodies = [
        b for b in root.findall("fb:body", NS) if b.attrib.get("name") != "notes"
    ]

    main_html = ""
    for body in normal_bodies:
        # Многотомные "компиляции" склеивают несколько книг как отдельные
        # <body>, и оглавление ссылается прямо на id САМОГО body (напр.
        # <body id="vol_1">), а не на id секции внутри него — без этого
        # атрибута такие якоря ведут в никуда.
        body_id = xml_id(body)
        body_id_attr = f' id="{html.escape(body_id)}"' if body_id else ""
        main_html += f"<main{body_id_attr}>\n"
        for child in body:
            main_html += render_block(child, binaries, 2, note_ids, note_backrefs,
                                      False, anchor_counter)
        main_html += "</main>\n"

    notes_html = render_notes(root, binaries, note_ids, note_backrefs)

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
{_READER_CSS}
</style>
</head>
<body>
<script>{_READER_TOOLBAR_JS}</script>
{_READER_TOOLBAR_HTML}
<div class="book-header">
<h1>{html.escape(title)}</h1>
{render_coverpage(coverpage, binaries)}
{render_description(meta, annotation, binaries)}
</div>
{main_html}
{notes_html}
<script>{_build_progress_tracker_js(progress_url, resume_anchor)}</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Конвертирует FB2/FB2.ZIP в HTML.")
    parser.add_argument("input", help="Входной файл, например buch.fb2 или buch.fb2.zip")
    parser.add_argument("-o", "--output", help="Выходной файл, например buch.html")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".html")
    fb2_to_html(input_path, output_path)
    print(f"HTML создан: {output_path}")


if __name__ == "__main__":
    main()
