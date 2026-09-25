"""Регрессия для `Pass2SeriesFilename._postpass_arc_numbering()`'s
"Финал" step — docs/quality-roadmap.md, баг №122.

Реальный случай (Серия - «Колычев. Лучшая криминальная драма»\\):
"Колычев - Максим Юрьев 2. Стрела Амура 9-го калибра.fb2" — series_number
уже надёжно установлен из FB2 <sequence number="2">, но "Финал"-шаг
искал ВТОРОЕ число где-то дальше в стеме после заголовка (задумано для
случаев вида "Серия N. Подсерия M") и находил "9" из "9-го калибра" —
просто часть названия книги, не номер тома — и слепо ПЕРЕЗАПИСЫВАЛ
надёжное значение "2" на неверное "9".
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>{title}</book-title>
{sequence}
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_, filename, title, seq_name=None, sn=None):
    seq = f'<sequence name="{seq_name}" number="{sn}"/>' if seq_name else ""
    p = dir_ / filename
    p.write_text(_FB2.format(title=title, sequence=seq), encoding="utf-8")
    return p


class TestArcNumberingDoesNotOverwriteTrustedSeriesNumber:
    def test_metadata_series_number_survives_unrelated_second_number_in_title(self, tmp_path):
        # Файлы намеренно лежат ПРЯМО в tmp_path (без папки-автора) — это
        # и воспроизводит баг: с папкой-автором сверху proposed_author
        # извлекается иначе, и цепочка (Шаг 1 → author_roots → "Финал")
        # не запускается тем же путём.
        # Первая книга — без своего второго числа в заголовке, задаёт
        # проверяемой связке "Максим Юрьев" известность (author_roots).
        _write_book(
            tmp_path, "Волков - Максим Юрьев 1. Кино кончилось. Дублей не будет.fb2",
            "Максим Юрьев 1. Кино кончилось. Дублей не будет",
        )
        # Вторая книга — series_number=2 надёжно из <sequence>, но
        # заголовок содержит НЕСВЯЗАННОЕ число "9" ("9-го калибра").
        _write_book(
            tmp_path, "Волков - Максим Юрьев 2. Стрела Амура 9-го калибра.fb2",
            "Максим Юрьев 2. Стрела Амура 9-го калибра",
            seq_name="Капитан полиции Максим Юрьев", sn="2",
        )

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)
        rec2 = next(r for r in records if "Стрела Амура" in r.file_path)

        assert rec2.series_number == "2", (
            f"series_number должен остаться 2 (из metadata), а не '9' из "
            f"названия — получено {rec2.series_number!r} (source={rec2.series_number_source!r})"
        )
