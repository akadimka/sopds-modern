"""Поиск сборников, испорченных багом «дописывание тома режет прежние тома
по главам» (fb2_compiler до v2.35.001). Только чтение: файлы не меняет.

    python scripts/find_damaged_compilations.py /srv/sopds-books
    python scripts/find_damaged_compilations.py "D:\\EBook Library" --csv damaged.csv

Наш сборник узнаётся по <body id="vol_N"> на каждый том. Испорченный —
если в нём есть хотя бы один признак:
  toc_in_vol  — внутри «тома» лежит оглавление со ссылками на тома (#vol_N):
                прежний блок «Содержание» стал томом — надёжный признак;
  bad_titles  — тома с названием «Глава …», «Содержание», «Оглавление»:
                прежние тома разрезаны по главам (без toc_in_vol — проверить
                глазами: бывают и настоящие тома с таким названием).
Описание и что делать дальше — docs/damaged-compilations.md.

Нужна только стандартная библиотека Python 3.8+ (на сервере — `python3`).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import zipfile

VOL_BODY = re.compile(r'<(?:fb:)?body[^>]*\bid\s*=\s*["\']vol_(\d+)["\'][^>]*>(.*?)</(?:fb:)?body>',
                      re.S | re.I)
TITLE = re.compile(r'<title>\s*<p>(.*?)</p>', re.S)
TOC_LINK = re.compile(r'l:href\s*=\s*["\']#vol_\d+')
BAD_TITLE = re.compile(r'^(глава|содержание|оглавление)\b', re.I)


def read_fb2(path: str) -> str:
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith('.fb2')), None)
            return z.read(name).decode('utf-8', 'replace') if name else ''
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'replace')


def inspect(xml: str):
    """None — не наш сборник; иначе (томов, toc_in_vol, bad_titles, названия томов)."""
    if 'vol_' not in xml:
        return None
    bodies = VOL_BODY.findall(xml)
    if not bodies:
        return None
    titles, toc_in_vol = [], 0
    for _, body in bodies:
        m = TITLE.search(body)
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
        titles.append(re.sub(r'^\d+\.\s+', '', title))
        toc_in_vol += bool(TOC_LINK.search(body))
    bad_titles = sum(bool(BAD_TITLE.match(t)) for t in titles)
    return len(bodies), toc_in_vol, bad_titles, titles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('root', help='папка библиотеки')
    parser.add_argument('--csv', help='сохранить список в CSV (UTF-8)')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    scanned = ours = errors = 0
    found = []
    for dirpath, _, files in os.walk(args.root):
        for name in files:
            low = name.lower()
            if not (low.endswith('.fb2') or low.endswith('.fb2.zip')):
                continue
            scanned += 1
            path = os.path.join(dirpath, name)
            try:
                result = inspect(read_fb2(path))
            except Exception as e:                      # битый zip, нет прав — сообщить и дальше
                errors += 1
                print(f'ОШИБКА {path}: {e}', file=sys.stderr)
                continue
            if result is None:
                continue
            ours += 1
            volumes, toc_in_vol, bad_titles, titles = result
            if toc_in_vol or bad_titles:
                found.append({
                    'modified': time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path))),
                    'verdict': 'испорчен' if toc_in_vol else 'проверить',
                    'volumes': volumes, 'toc_in_vol': toc_in_vol, 'bad_titles': bad_titles,
                    'path': os.path.relpath(path, args.root),
                    'titles': ' | '.join(titles),
                })

    found.sort(key=lambda r: (r['verdict'] != 'испорчен', r['modified']))
    for r in found:
        print(f"{r['verdict']:9}  {r['modified']}  томов={r['volumes']:<3} toc_in_vol={r['toc_in_vol']} "
              f"bad_titles={r['bad_titles']:<3} {r['path']}")
        print(f"           тома: {r['titles'][:300]}")
    damaged = sum(r['verdict'] == 'испорчен' for r in found)
    print(f'\nфайлов FB2: {scanned}; наших сборников: {ours}; испорчено: {damaged}; '
          f'проверить: {len(found) - damaged}; ошибок чтения: {errors}')
    if args.csv:
        with open(args.csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(found[0]) if found else ['path'])
            writer.writeheader()
            writer.writerows(found)
        print(f'список: {args.csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
