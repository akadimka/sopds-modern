# -*- coding: utf-8 -*-
"""Строит tests/data/compile_completeness/ — синтетический (не из реальной
библиотеки) fixture-набор для tests/integration/fb2parser_core/
test_compile_completeness.py: три fb2-файла с контролируемым размером,
воспроизводящие баг "широкий номинальный диапазон побеждает при выборе
лучшей предкомпиляции, даже если реально содержит меньше текста"
(fb2_compiler.py, выбор best_pre в compile_group).
"""
from pathlib import Path

DST = Path(__file__).resolve().parent.parent / "tests" / "data" / "compile_completeness" / "Автор Тест"

TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title>
<sequence name="Серия Тест" number="{num}"/>
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>{filler}</p></section>
</body>
</FictionBook>
"""

FILES = [
    # (имя файла, заголовок, номер/диапазон, "объём" — сколько раз повторить строку-заполнитель)
    ("Автор Тест - Серия Тест (Серия Тест. Пенталогия).fb2", "Серия Тест. Пенталогия", "1", 2000),
    ("Автор Тест - Серия Тест (Серия Тест 1-3).fb2", "Серия Тест 1-3", "1-3", 6000),
    ("Автор Тест - Серия Тест (Серия Тест 4-5).fb2", "Серия Тест 4-5", "4-5", 6000),
]


def main():
    DST.mkdir(parents=True, exist_ok=True)
    for name, title, num, filler_repeat in FILES:
        filler = "Тестовый текст fixture для проверки полноты содержимого. " * filler_repeat
        content = TEMPLATE.format(title=title, num=num, filler=filler)
        (DST / name).write_text(content, encoding="utf-8")
        print(name, len(content.encode("utf-8")), "bytes")


if __name__ == "__main__":
    main()
