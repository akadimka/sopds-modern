"""Регрессия для `Pass2SeriesFilename._apply_folder_series()` /
`Pass4Consensus.execute()`'s "Cleaning up folder_hierarchy series with
embedded author names" — docs/quality-roadmap.md, баг №4/№121.

Реальный случай (Test1/Test2, Серия - «Колычев. Лучшая криминальная
драма»\\): папка-«антология» одного автора называется «Серия - «Фамилия.
Текст»» — декоративная кавычка-«ёлочка» приклеена к фамилии БЕЗ пробела
("«Колычев." — единый токен, не "Колычев" + "«"). Обе проверки "папка
содержит имя автора" (word-token based: pass2's `_folder_contains_author`
и pass4's `author_prefixes`/`series_prefixes`) режут строку на токены по
пробелу/точке/тире, не отрезая пунктуацию по краям — токен "«колычев" не
совпадает со словом "колычев" ни через ==, ни через startswith в любую
сторону. Обе проверки решают, что папка НЕ содержит автора, и весь текст
папки принимается за полноценное название серии (`folder_hierarchy`) —
даже для файлов, у которых имя файла содержит куда более точную под-
серию ("Максим Юрьев N. ...", "Мент в законе N"). Поскольку
`folder_hierarchy` считается достаточно авторитетным источником, чтобы
пайплайн даже не пытался распознать серию по имени файла, реальная под-
серия никогда не вычисляется вовсе.

Баг воспроизводится ТОЛЬКО при сканировании от корня БИБЛИОТЕКИ (папка-
антология становится родительским каталогом файла) — при сканировании
одной этой папки изолированно (она сама — корень) баг не проявляется,
поэтому тест намеренно сканирует `tmp_path` (родитель), а не саму
папку-антологию.
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


class TestPunctuationGluedAuthorFolderNotTreatedAsSeries:
    def test_filename_series_survives_glued_quote_author_folder(self, tmp_path):
        anthology_dir = tmp_path / "Серия - «Волков. Вымышленная антология»"
        anthology_dir.mkdir()
        # Без <sequence> в метаданных — как реальный "Максим Юрьев 1...fb2".
        _write_book(
            anthology_dir, "Волков - Максим Юрьев 1. Кино кончилось.fb2",
            "Максим Юрьев 1. Кино кончилось",
        )
        _write_book(
            anthology_dir, "Волков - Максим Юрьев 2. Стрела Амура.fb2",
            "Максим Юрьев 2. Стрела Амура",
        )
        # С <sequence>, НЕ совпадающим с папкой — как реальный "Мент в
        # законе N...fb2" (metadata_series="Мент в законе").
        _write_book(
            anthology_dir, "Волков - Мент в законе 14. Круче, чем оружие.fb2",
            "Круче, чем оружие", seq_name="Мент в законе", sn="12",
        )

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)
        by_name = {r.file_path.split("\\")[-1]: r for r in records}

        r1 = by_name["Волков - Максим Юрьев 1. Кино кончилось.fb2"]
        r2 = by_name["Волков - Максим Юрьев 2. Стрела Амура.fb2"]
        r3 = by_name["Волков - Мент в законе 14. Круче, чем оружие.fb2"]

        for r in (r1, r2, r3):
            assert r.series_source != "folder_hierarchy", (
                f"{r.file_path}: папка-антология с приклеенной кавычкой к фамилии "
                "не должна распознаваться как настоящее название серии"
            )

        assert "Максим Юрьев" in (r1.proposed_series or "")
        assert "Максим Юрьев" in (r2.proposed_series or "")
        assert "Мент в законе" in (r3.proposed_series or "")
