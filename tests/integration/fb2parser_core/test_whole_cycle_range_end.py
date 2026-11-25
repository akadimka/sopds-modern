"""Найдено пользователем в компиляции «Акунин Борис / Смерть на брудершафт»
(2026-11-25): «Октология» из «Весь цикл в одном томе» (1-7) и «Фильма 7-8».

Цикл — 10 фильм в файлах 1, 2, 3, 4, «Фильма 5-6», «Фильма 7-8» (номер 7
в метаданных), «Фильма 9-10» и «Весь цикл в одном томе» без номера. Верхняя
граница цельного тома бралась как наибольшая позиция группы — 7 (начало
«7-8»), так что «Фильма 7-8» шёл в сборник вторым томом: текст 7–8 дважды.
Конец диапазона «7-8» — тоже известная позиция: цельный том покрывает всё,
компилировать нечего, остаётся только чистка дублей.
"""
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

SERIES = "Смерть на брудершафт"
_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>detective</genre>
<author><first-name>Борис</first-name><last-name>Акунин</last-name></author>
<book-title>{title}</book-title>{seq}
</title-info></description>
<body>{text}</body>
</FictionBook>
"""


def _film(n):
    return f"<section><title><p>Фильма {n}</p></title>" + "".join(
        f"<p>Фильма {n}, абзац {i}: текст романа-кино для проверки.</p>" for i in range(40)) + "</section>"


def _seq(*numbers):
    return "".join(f'<sequence name="{SERIES}" number="{n}"/>' for n in numbers)


def test_whole_cycle_covers_range_end_of_last_numbered_file(tmp_path):
    folder = tmp_path / "Акунин Борис - Сборник" / SERIES
    folder.mkdir(parents=True)
    files = [
        ("Akunin_Smert_na_brudershaft_1_Mladenets_i_chert.fb2", "Младенец и черт", _seq(1), [1]),
        ("Akunin_Smert_na_brudershaft_2_Muka_razbitogo_serdtsa.fb2", "Мука разбитого сердца", _seq(2), [2]),
        ("Akunin_Smert_na_brudershaft_3_Letayuschiy_slon.fb2", "Летающий слон", _seq(3), [3]),
        ("Akunin_Smert_na_brudershaft_4_Deti_Lunyi.fb2", "Дети Луны", _seq(4), [4]),
        ("Akunin_Smert_na_brudershaft_5_Smert_na_brudershaft_Filma_5-6.fb2",
         f"{SERIES} (фильма пятая и шестая)", _seq(5, 6), [5, 6]),
        ("Akunin_Smert_na_brudershaft_7_Smert_na_brudershaft_Filma_7-8.fb2",
         f"{SERIES} (фильма 7-8)", _seq(7), [7, 8]),
    ]
    for name, title, seq, films in files:
        (folder / name).write_text(
            _FB2.format(title=title, seq=seq, text="".join(_film(n) for n in films)), encoding="utf-8")
    whole = f"Б. Акунин - {SERIES} (Весь цикл в одном томе).fb2"
    (folder / whole).write_text(
        _FB2.format(title=f"Весь цикл «{SERIES}» в одном томе.", seq="",
                    text="".join(_film(n) for n in range(1, 9))), encoding="utf-8")

    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    [group] = FB2CompilerService().find_groups(records, tmp_path)

    assert group.cleanup_only
    assert [p.name for p in group.kept_paths] == [whole]
    assert sorted(p.name for p in group.duplicate_paths) == sorted(name for name, *_ in files)
