"""Поиск дублей книг в библиотеке по тексту. Только чтение: файлы не меняет.

    python3 find_library_duplicates.py /srv/sopds-books --csv dups.csv > dups.txt

Дубль ищется не по имени, а по содержимому: одна и та же книга под разными
названиями, авторами, в разных папках, разных изданиях, внутри сборника.

Отпечаток книги — абзацы текста (длиннее 30 букв, без регистра и знаков),
из которых берётся примерно каждый 32-й — одинаково для всех файлов (по
значению хэша абзаца), так что выборки разных файлов сравнимы. Пара файлов
с общими абзацами выборки — кандидат; для неё считается, какая доля выборки
одного есть в другом. Виды:
  копия          — содержимое файлов совпадает побайтно (после распаковки);
  тот же текст   — каждый почти целиком (от 90 %) в другом: другое
                   оформление, обложка, кодировка, другое имя;
  входит в       — меньший почти целиком (от 90 %) в большем: том внутри
                   сборника, неполное издание внутри полного;
  пересекаются   — от 50 %: часть общего текста (сборник рассказов, другое
                   издание с правками) — смотреть глазами.

Абзацы, которые встречаются больше чем в MAX_SHARED файлах (реклама
раздачи, «Nota bene», типовые предисловия), в сравнении не участвуют.
Нужна только стандартная библиотека Python 3.8+ (на сервере — `python3`).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from multiprocessing import Pool

SAMPLE = 32            # берём абзац, если хэш делится на SAMPLE
MIN_LEN = 30           # короче — не абзац текста (заголовки, «* * *», реплики)
MIN_SAMPLED = 8        # меньше абзацев в выборке — по тексту не сравниваем (только «копия»)
MAX_SHARED = 25        # абзац в стольких файлах и больше — шаблонный, не учитываем
SAME, INSIDE, OVERLAP = 0.9, 0.9, 0.5

P_RE = re.compile(r'<p(?:\s[^>]*?)?(?<!/)>(.*?)</p>', re.S)
TAG_RE = re.compile(r'<[^>]+>')
NONWORD_RE = re.compile(r'\W+')
BIN_RE = re.compile(r'<binary\b.*?</binary>', re.S)
DECL_RE = re.compile(rb'<\?xml[^>]*encoding=["\']([\w-]+)')
TITLE_RE = re.compile(r'<book-title>(.*?)</book-title>', re.S)


def read_bytes(path: str) -> bytes:
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist() if n.lower().endswith('.fb2')), None)
            return z.read(name) if name else b''
    with open(path, 'rb') as f:
        return f.read()


def fingerprint(path: str):
    """(путь, md5 содержимого, абзацев, название, выборка хэшей) или (путь, ошибка)."""
    try:
        raw = read_bytes(path)
        m = DECL_RE.search(raw[:300])
        try:
            text = raw.decode(m.group(1).decode() if m else 'utf-8', 'replace')
        except LookupError:
            text = raw.decode('utf-8', 'replace')
        text = BIN_RE.sub('', text)
        t = TITLE_RE.search(text[:20000])
        title = ' '.join(html.unescape(TAG_RE.sub(' ', t.group(1))).split())[:120] if t else ''
        sample, paras = set(), 0
        for p in P_RE.findall(text):
            norm = NONWORD_RE.sub('', html.unescape(TAG_RE.sub(' ', p)).lower())
            if len(norm) < MIN_LEN:
                continue
            paras += 1
            h = int.from_bytes(hashlib.blake2b(norm.encode('utf-8'), digest_size=8).digest(), 'big')
            if h % SAMPLE == 0:
                sample.add(h)
        return path, hashlib.md5(raw).hexdigest(), paras, title, sample
    except Exception as e:                       # битый zip, нет прав — в отчёт ошибок
        return path, f'{type(e).__name__}: {e}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('root', help='папка библиотеки')
    parser.add_argument('--csv', help='все найденные пары в CSV (UTF-8)')
    parser.add_argument('--workers', type=int, default=1, help='процессов разбора (по умолчанию 1)')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    paths = []
    for dirpath, _, names in os.walk(args.root):
        for name in names:
            low = name.lower()
            if low.endswith('.fb2') or low.endswith('.fb2.zip'):
                paths.append(os.path.join(dirpath, name))
    paths.sort()
    started = time.time()
    print(f'файлов: {len(paths)}', file=sys.stderr, flush=True)

    rel = {}
    md5, n_paras, titles, samples, errors = {}, {}, {}, {}, []
    with Pool(args.workers) as pool:
        for i, res in enumerate(pool.imap_unordered(fingerprint, paths, chunksize=8), 1):
            if len(res) == 2:
                errors.append(res)
            else:
                p, digest, paras, title, sample = res
                md5[p], n_paras[p], titles[p], samples[p] = digest, paras, title, sample
            if i % 500 == 0:
                print(f'  прочитано {i}/{len(paths)} за {int(time.time() - started)} с', file=sys.stderr, flush=True)
    for p in md5:
        rel[p] = os.path.relpath(p, args.root)

    # Пары: одинаковое содержимое — сразу; по тексту — через общий индекс выборок.
    pairs = {}
    by_md5 = defaultdict(list)
    for p, d in md5.items():
        by_md5[d].append(p)
    for group in by_md5.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                pairs[tuple(sorted((group[i], group[j])))] = ('копия', 1.0, 1.0)

    index = defaultdict(list)
    for p, s in samples.items():
        if len(s) >= MIN_SAMPLED:
            for h in s:
                index[h].append(p)
    shared = Counter()
    for files in index.values():
        if 2 <= len(files) < MAX_SHARED:
            for i in range(len(files)):
                for j in range(i + 1, len(files)):
                    shared[tuple(sorted((files[i], files[j])))] += 1
    del index
    for (a, b), n in shared.items():
        if (a, b) in pairs or n < 3:
            continue
        a_in_b, b_in_a = n / len(samples[a]), n / len(samples[b])
        if min(a_in_b, b_in_a) >= SAME:
            kind = 'тот же текст'
        elif max(a_in_b, b_in_a) >= INSIDE:
            kind = 'входит в'
        elif max(a_in_b, b_in_a) >= OVERLAP:
            kind = 'пересекаются'
        else:
            continue
        pairs[(a, b)] = (kind, a_in_b, b_in_a)

    order = {'копия': 0, 'тот же текст': 1, 'входит в': 2, 'пересекаются': 3}
    rows = []
    for (a, b), (kind, a_in_b, b_in_a) in pairs.items():
        if kind == 'входит в' and a_in_b < b_in_a:      # меньший — первым
            a, b, a_in_b, b_in_a = b, a, b_in_a, a_in_b
        rows.append({
            'kind': kind, 'genre': rel[a].replace('\\', '/').split('/')[0],
            'a_in_b': f'{a_in_b:.2f}', 'b_in_a': f'{b_in_a:.2f}',
            'a_paras': n_paras[a], 'b_paras': n_paras[b],
            'a_size': os.path.getsize(a), 'b_size': os.path.getsize(b),
            'a': rel[a], 'b': rel[b], 'a_title': titles[a], 'b_title': titles[b],
        })
    rows.sort(key=lambda r: (order[r['kind']], r['genre'], r['a']))

    # Группы: файлы, связанные «копией», «тем же текстом» или «входит в», —
    # одна книга во всех её экземплярах (для разбора по одной).
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), (kind, _, _) in pairs.items():
        if kind != 'пересекаются':
            parent[find(a)] = find(b)
    members = defaultdict(list)
    for p in list(parent):
        members[find(p)].append(p)
    groups = sorted((sorted(m, key=lambda p: rel[p]) for m in members.values() if len(m) > 1),
                    key=lambda m: rel[m[0]])

    kinds = Counter(r['kind'] for r in rows)
    genres = Counter((r['genre'], r['kind']) for r in rows)
    print(f'файлов: {len(paths)}; прочитано: {len(md5)}; ошибок чтения: {len(errors)}; '
          f'пар: {len(rows)} — ' + ', '.join(f'{k}: {kinds[k]}' for k in order if kinds[k]))
    print(f'групп (одна книга в нескольких файлах): {len(groups)}, файлов в них: {sum(map(len, groups))}')
    print('\nпары по жанрам (жанр папки первого файла):')
    for genre in sorted({g for g, _ in genres}):
        print(f'  {genre:28} ' + ', '.join(f'{k}: {genres[(genre, k)]}' for k in order if genres[(genre, k)]))
    print(f'\n=== группы: {len(groups)}')
    for n, group in enumerate(groups, 1):
        print(f'\n[{n}] {len(group)} файла(ов)')
        for p in group:
            print(f'    {n_paras[p]:6} абз. {os.path.getsize(p) // 1024:7} КБ  {rel[p]}')
    for kind in order:
        part = [r for r in rows if r['kind'] == kind]
        if not part:
            continue
        print(f'\n=== {kind}: {len(part)}')
        for r in part:
            print(f"{r['a_in_b']}/{r['b_in_a']}  {r['a']}\n           {r['b']}")
    for path, err in errors:
        print(f'ОШИБКА {path}: {err}', file=sys.stderr)
    if args.csv:
        with open(args.csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['kind'])
            writer.writeheader()
            writer.writerows(rows)
    print(f'\nвремя: {int(time.time() - started)} с', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
