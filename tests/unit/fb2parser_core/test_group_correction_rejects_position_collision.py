"""Регрессия для `FB2CompilerService.find_groups()`'s "групповая
коррекция" (баг №68) — docs/quality-roadmap.md, баг №109.

Реальный случай (замечен пользователем в превью компиляции, Test1):
Шарапов Валерий / "Контрразведка", 17 файлов — том 13 несёт БИТУЮ
метадату (`<sequence number="12">`, опечатка издателя/конвертера), а
позиция 12 уже занята ДРУГИМ, настоящим 12-м томом (свой номер из имени
файла, метаданных без номера вовсе). Существующий guard бага №68
("потолок правдоподобия") ловит только СЛИШКОМ БОЛЬШИЕ битые числа
(реальный случай №68 — "32"/"99" от Telegram Bot) — но "12" здесь
абсолютно правдоподобно само по себе, просто уже занято. Коррекция
применялась, откатывая верный filename-номер тома 13 (13) на битую
метадату (12) — создавая неразрешимую коллизию с настоящим томом 12,
из-за которой серия рвалась на "1-12"/"14-17" вместо единой "1-17".

Отличие от бага №68 (test_group_correction_plausibility_guard.py):
там скорректированное число ни с кем не конфликтует (просто "слишком
далеко"); здесь число правдоподобно, но конфликтует с ДРУГИМ файлом —
поэтому нужна отдельная, дополнительная проверка, а не более широкий
потолок.
"""
from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Тест</first-name><last-name>Автор</last-name></author>
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


def _write_book(dir_, filename, title, series="Контрразведка", num=""):
    sequence = f'<sequence name="{series}" number="{num}"/>' if num else ""
    p = dir_ / filename
    p.write_text(_FB2.format(title=title, sequence=sequence), encoding="utf-8")
    return p


class TestGroupCorrectionRejectsCollisionWithAnotherBooksPosition:
    def test_plausible_but_colliding_metadata_number_not_applied(self, tmp_path):
        author_dir = tmp_path / "Автор Тест"
        author_dir.mkdir()
        # Тома 1-10: имя файла и метадата согласны — большинство доверяет мете.
        for n in range(1, 11):
            _write_book(author_dir, f"Автор Тест - {n:02d}.Книга {n}.fb2",
                        f"Книга {n}", num=str(n))
        # Том 11: без метаданных вовсе — берёт номер только из имени файла.
        _write_book(author_dir, "Автор Тест - 11.Книга одиннадцать.fb2",
                    "Книга одиннадцать")
        # Том 12: тоже без метаданных — номер только из имени файла.
        _write_book(author_dir, "Автор Тест - 12.Книга двенадцать.fb2",
                    "Книга двенадцать")
        # Том 13: имя файла даёт 13, но метадата БИТА и ошибочно повторяет 12
        # (правдоподобное само по себе число — коллизия, не "слишком большое").
        _write_book(author_dir, "Автор Тест - 13.Книга тринадцать.fb2",
                    "Книга тринадцать", num="12")

        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Автор Тест"]
        assert len(matches) == 1, f"expected 1 merged group (1-13), got {len(matches)}"

        book12 = next(b for b in matches[0].books if "двенадцать" in b.abs_path.name)
        book13 = next(b for b in matches[0].books if "тринадцать" in b.abs_path.name)
        assert book12.sort_key[1] == 12
        assert book13.sort_key[1] == 13
