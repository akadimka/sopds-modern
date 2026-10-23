"""Восстановление разбивки на тома в сборниках, испорченных багом «дописывание
тома режет прежние тома по главам» (fb2_compiler до v2.35.001).
Описание — docs/damaged-compilations.md.

    python scripts/repair_damaged_compilations.py /srv/sopds-books --list damaged.csv
    python scripts/repair_damaged_compilations.py /srv/sopds-books --list damaged.csv \\
        --apply approved.txt --backup /var/tmp/damaged-backup

Без --apply только читает и печатает, на какие тома разложится каждый файл.
С --apply переписывает на месте только файлы из approved.txt (по пути на
строку, как в CSV), каждый — после копии в --backup.

Как испорчен файл. Старый сборщик разбирал прежний сборник по <section>:
блок «Содержание» стал томом (T — тело со ссылками на #vol_N), раздел с
обложкой каждого прежнего тома — тоже томом без текста (C), каждая глава —
отдельным томом с названием главы. Восстанавливается только ясный случай:
одно такое оглавление на n томов, сразу за ним n обложек, у каждого тома
есть текст, а число томов вместе с целыми томами до и после сходится с
диапазоном <sequence number>. Названия томов — из этого оглавления, глава
получает своё название обратно, текст не меняется (проверяется сравнением
всех абзацев до и после).

Путь файла не меняется — владельцы файла (учёт по пути) остаются прежними.
Нужна только стандартная библиотека Python 3.8+ (на сервере — `python3`).
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import shutil
import sys
import tempfile
import xml.dom.minidom
import zipfile

BODY = re.compile(r'<body\b([^>]*)>(.*?)</body>', re.S)
VOL_ID = re.compile(r'\bid="vol_\d+"')
TITLE = re.compile(r'\s*<title>(.*?)</title>', re.S)
TOC_LINK = re.compile(r'<a[^>]*l:href="#vol_\d+"[^>]*>(.*?)</a>', re.S)
COVER_SECTION = re.compile(r'\s*<section>\s*<image l:href="#([^"]+)"\s*/>\s*</section>', re.S)
SECTION_OPEN = re.compile(r'<section\b[^>]*>')
NUMBER = re.compile(r'^\d+\.\s+')
SERVICE_TITLE = re.compile(r'.+ \(\d+\)$')       # «{имя файла} (N)» — у раздела не было названия
PARA = re.compile(r'<p(?:\s[^>]*?)?(?<!/)>(.*?)</p>', re.S)    # не <p/>
IMAGE = re.compile(r'l:href="#([^"]+)"')


def _text(fragment: str) -> str:
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', fragment)).split())


class Body:
    """Тело тома сборника: kind — T (оглавление), C (только обложка), t (текст)."""

    def __init__(self, inner: str):
        m = TITLE.match(inner)
        first_p = PARA.search(m.group(1)) if m else None
        self.title = NUMBER.sub('', _text(first_p.group(1))) if first_p else ''
        self.content = inner[m.end():] if m else inner
        self.links = [NUMBER.sub('', _text(x)) for x in TOC_LINK.findall(self.content)]
        self.paras = len(PARA.findall(self.content))
        self.has_cover = bool(COVER_SECTION.match(self.content))
        if self.links and self.paras <= len(self.links) + 3:
            self.kind = 'T'
        elif self.paras == 0:
            self.kind = 'C' if self.has_cover else 'E'
        else:
            self.kind = 't'


class Volume:
    def __init__(self, title: str, bodies: list, rebuilt: bool):
        self.title, self.bodies, self.rebuilt = title, bodies, rebuilt

    @property
    def paras(self) -> int:
        return sum(b.paras for b in self.bodies)


def read_fb2(path: str) -> str:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith('.fb2')), None)
            return z.read(name).decode('utf-8') if name else ''
    with open(path, 'rb') as f:
        return f.read().decode('utf-8')


def _vol_bodies(xml_text: str):
    return [m for m in BODY.finditer(xml_text) if VOL_ID.search(m.group(1))]


def plan(xml_text: str):
    """(список Volume, None) — если разбивку можно восстановить; иначе (None, причина)."""
    head = xml_text[:xml_text.find('<body')]
    seq = re.search(r'<sequence\b[^>]*\bnumber="(\d+)(?:-(\d+))?"', head)
    if not seq:
        return None, 'нет номера серии'
    expected = int(seq.group(2) or seq.group(1)) - int(seq.group(1)) + 1
    bodies = [Body(m.group(2)) for m in _vol_bodies(xml_text)]
    tocs = [i for i, b in enumerate(bodies) if b.kind == 'T']
    if len(tocs) != 1:
        return None, f'вложенных оглавлений {len(tocs)}'
    ti = tocs[0]
    names = bodies[ti].links
    n = len(names)
    covers = [i for i in range(ti + 1, len(bodies)) if bodies[i].kind == 'C']
    if not covers or covers[0] != ti + 1:
        return None, 'за оглавлением не обложка тома'
    if len(covers) != n:
        return None, f'томов в оглавлении {n}, обложек {len(covers)}'
    if any(SERVICE_TITLE.match(x) or not x for x in names):
        return None, 'в оглавлении служебные названия томов (файл портился не раз)'
    after = expected - ti - n
    if after < 0:
        return None, f'томов больше диапазона серии на {-after}'
    tail = len(bodies) - after
    if after and not all(b.has_cover for b in bodies[tail:]):
        return None, f'последние {after} тел не похожи на целые тома'
    volumes = [Volume(b.title, [b], False) for b in bodies[:ti]]
    bounds = covers + [tail]
    for j in range(n):
        span = bodies[bounds[j]:bounds[j + 1]]
        if len(span) < 2 or any(b.kind != 't' for b in span[1:]):
            return None, f'у тома «{names[j]}» нет текста или в нём лишнее'
        volumes.append(Volume(names[j], span, True))
    volumes += [Volume(b.title, [b], False) for b in bodies[tail:]]
    return volumes, None


def _chapter(body: Body, service_stem: str) -> str:
    """Тело-«том» с главой → разделы главы с её названием на месте."""
    content = body.content
    m = COVER_SECTION.match(content)
    if m and content[m.end():].count(f'#{m.group(1)}"'):
        content = content[m.end():]                 # картинка главы, продублированная как обложка
    title = body.title
    if not title or title.startswith(service_stem):
        return content.strip()
    first = SECTION_OPEN.search(content)
    if not first:
        return content.strip()
    tag = f'<title><p>{html.escape(title, quote=False)}</p></title>'
    return (content[:first.end()] + tag + content[first.end():]).strip()


def _volume_content(vol: Volume) -> str:
    if not vol.rebuilt:
        return vol.bodies[0].content.strip()
    cover = COVER_SECTION.match(vol.bodies[0].content)
    stem = re.sub(r' \(\d+\)$', '', vol.bodies[0].title)
    parts = [cover.group(0).strip()] if cover else []
    parts += [_chapter(b, stem) for b in vol.bodies[1:]]
    return '\n'.join(parts)


def rebuild(xml_text: str, volumes) -> str:
    """Тот же файл, тома — по volumes, оглавление — заново (как _build_fb2)."""
    found = list(BODY.finditer(xml_text))
    start, end = found[0].start(), _vol_bodies(xml_text)[-1].end()
    rest = [m.group(0) for m in found if m.start() >= end]       # сноски и т. п. после томов
    toc = '\n'.join(f'<p><a l:href="#vol_{i}">{i}. {html.escape(v.title, quote=False)}</a></p>'
                    for i, v in enumerate(volumes, 1))
    parts = [f'<body>\n<section>\n<title><p>Содержание</p></title>\n{toc}\n</section>\n</body>']
    for i, v in enumerate(volumes, 1):
        parts.append(f'<body id="vol_{i}">\n<title><p>{i}. {html.escape(v.title, quote=False)}</p></title>\n'
                     f'{_volume_content(v)}\n</body>')
    tail_end = found[-1].end() if rest else end
    raw = xml_text[:start] + '\n'.join(parts + rest) + xml_text[tail_end:]
    xml.dom.minidom.parseString(raw.encode('utf-8'))      # только проверка: XML цел
    return raw


def _text_and_images(xml_text: str):
    """Абзацы вне заголовков и ссылки на картинки — то, что починка менять не должна."""
    paras, images = [], set()
    for m in _vol_bodies(xml_text):
        b = Body(m.group(2))
        if b.kind == 'T':
            continue
        content = re.sub(r'<title>.*?</title>', '', b.content, flags=re.S)
        paras += [_text(p) for p in PARA.findall(content)]
        images |= set(IMAGE.findall(b.content))
    return paras, images


def verify(before: str, after: str) -> None:
    """AssertionError, если текст или картинки томов изменились."""
    pb, ib = _text_and_images(before)
    pa, ia = _text_and_images(after)
    assert [p for p in pb if p] == [p for p in pa if p], 'текст томов изменился'
    assert ib == ia, 'картинки томов изменились'


def describe(volumes) -> str:
    lines = []
    for i, v in enumerate(volumes, 1):
        if not v.rebuilt:
            lines.append(f'  {i:>3}. {v.title[:60]:60} без изменений, {v.paras} абз.')
            continue
        chapters = [b.title for b in v.bodies[1:]]
        lines.append(f'  {i:>3}. {v.title[:60]:60} из {len(chapters)} глав, {v.paras} абз.: '
                     f'«{chapters[0][:40]}» … «{chapters[-1][:40]}»')
    return '\n'.join(lines)


def write_fb2(path: str, xml_text: str) -> None:
    """Записать на место: во временный файл рядом, затем замена (права — прежние)."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix='.tmp')
    os.close(fd)
    try:
        data = xml_text.encode('utf-8')
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                name = next(n for n in z.namelist() if n.lower().endswith('.fb2'))
            with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr(name, data)
        else:
            with open(tmp, 'wb') as f:
                f.write(data)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('root', help='папка библиотеки')
    parser.add_argument('--list', required=True, help='CSV поиска (find_damaged_compilations.py --csv)')
    parser.add_argument('--apply', help='файл со списком путей, которые можно переписать')
    parser.add_argument('--backup', help='папка для копий перед записью (обязательна с --apply)')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.apply and not args.backup:
        parser.error('--apply только вместе с --backup')
    approved = None
    if args.apply:
        with open(args.apply, encoding='utf-8') as f:
            approved = {line.strip() for line in f if line.strip()}

    with open(args.list, encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r['verdict'].startswith('испорч')]
    can = written = 0
    for r in rows:
        rel = r['path']
        path = os.path.join(args.root, rel)
        try:
            before = read_fb2(path)
            volumes, why = plan(before)
            after = rebuild(before, volumes) if volumes else None
            if after:
                verify(before, after)
        except Exception as e:
            volumes, why = None, f'ошибка: {e}'
        if not volumes:
            print(f'вручную   {rel}\n          {why}\n')
            continue
        can += 1
        print(f'можно     {rel}\n{describe(volumes)}\n')
        if approved is not None and rel in approved:
            target = os.path.join(args.backup, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(path, target)
            write_fb2(path, after)
            written += 1
            print(f'          записан (копия: {target})\n')
    print(f'испорченных: {len(rows)}; восстановимо: {can}; вручную: {len(rows) - can}'
          + (f'; записано: {written}' if approved is not None else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
