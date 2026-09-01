# -*- coding: utf-8 -*-
"""Строит облегчённый fixture-набор tests/data/regen_library из реальной
библиотеки разработчика: сохраняет структуру папок/имён и FB2-метаданные
(author/title/sequence/genre), но вырезает текст книги (<body>) и
бинарники (<binary>, обложки), заменяя их короткими фиктивными заглушками
— чтобы не тащить в репозиторий авторский контент.

Разовый инструмент разработчика (не часть CI/тестового прогона). Каждая
запись в SOURCES — это реальная структура папок, на которой был найден
и починен конкретный баг regen_csv/fb2_compiler; см. докстринги тестов в
tests/integration/fb2parser_core/test_regen_edge_cases.py.

Использование:
    python scripts/build_regen_fixtures.py "C:\\path\\to\\your\\library"
"""
import re
import shutil
import sys
from pathlib import Path

DST_ROOT = Path(__file__).resolve().parent.parent / "tests" / "data" / "regen_library"

# (относительный путь папки-источника, лимит файлов или None = все, recursive)
SOURCES = [
    (r"Романович (Пастырь) Роман - Сборник\Пасть [=Обманувший смерть] (завершён)", None, False),
    (r"Романович (Пастырь) Роман - Сборник\Вне циклов", None, False),
    (r"Роберт Дж. Сойер", 15, False),
    (r"Русский фантастический боевик\Эльтеррус Иар (Тертышный Игорь)\Отзвуки серебряного ветра", None, True),
    (r"Русский фантастический боевик\Бессонов Алексей\Мир Алекса Королёва", None, True),
    (r"Серия - «Попаданец - СИ»\Смолин Павел", None, False),
    (r"lanpirot\Товарищ Чума", None, False),
    (r"lanpirot\Хоттабыч", None, False),
    (r"Аберкромби Джо", 8, False),  # только верхний уровень — подпапки ниже отдельно
    (r"Аберкромби Джо\В Серии -Fantasy World", None, False),
    (r"Аберкромби Джо\Земной Круг", None, False),
    (r"Аберкромби Джо\Море Осколков", None, False),
]

_BODY_RE = re.compile(r"<body\b[^>]*>.*?</body>", re.DOTALL | re.IGNORECASE)
_BINARY_RE = re.compile(r"<binary\b[^>]*>.*?</binary>", re.DOTALL | re.IGNORECASE)
_body_counter = [0]


def _stub_body(match: "re.Match") -> str:
    tag_open = match.group(0).split(">", 1)[0] + ">"
    _body_counter[0] += 1
    return (
        f"{tag_open}<title><p>Fixture stub {_body_counter[0]}</p></title>"
        f"<p>Тестовый текст для fixture-набора regen_csv. Не является "
        f"реальным содержимым книги.</p></body>"
    )


def strip_fb2(text: str) -> str:
    _body_counter[0] = 0
    text = _BINARY_RE.sub("", text)
    text = _BODY_RE.sub(_stub_body, text)
    return text


def main():
    if len(sys.argv) != 2:
        print("Использование: python scripts/build_regen_fixtures.py <путь_к_библиотеке>")
        sys.exit(1)
    src_root = Path(sys.argv[1])

    if DST_ROOT.exists():
        shutil.rmtree(DST_ROOT)
    DST_ROOT.mkdir(parents=True)

    total_files = 0
    total_in = 0
    total_out = 0
    for rel, limit, recursive in SOURCES:
        src_dir = src_root / rel
        if not src_dir.is_dir():
            print("MISSING SOURCE:", src_dir)
            continue
        fb2_files = sorted((src_dir.rglob if recursive else src_dir.glob)("*.fb2"))
        if limit:
            fb2_files = fb2_files[:limit]
        dst_dir = DST_ROOT / rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in fb2_files:
            raw = f.read_text(encoding="utf-8", errors="ignore")
            stripped = strip_fb2(raw)
            out_path = dst_dir / f.relative_to(src_dir)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(stripped, encoding="utf-8")
            total_in += len(raw.encode("utf-8"))
            total_out += len(stripped.encode("utf-8"))
            total_files += 1
        print(f"{rel}: {len(fb2_files)} files")

    print(f"\nTOTAL: {total_files} files, {total_in / 1024:.0f} KB -> {total_out / 1024:.0f} KB")


if __name__ == "__main__":
    main()
