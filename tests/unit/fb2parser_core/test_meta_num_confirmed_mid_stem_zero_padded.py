"""Регрессия для `FB2CompilerService._determine_sort_key()` — docs/quality-
roadmap.md, баг №71.

Реальный случай (замечен пользователем в CSV): Далин Макс / "Мир
Королей" — "Далин 04 Костер и Саламандра. Книга первая.fb2" несёт
корректный `series_number='4'` из метаданных, подтверждённый ведущим
номером в имени файла ("04"). Но заголовок книги — "Костер и Саламандра.
Книга 1" — слово "Книга 1" ошибочно интерпретировалось как позиция В
СЕРИИ (через `_extract_inline_volume_number`, roman_inline=1), потому что
проверка "meta_num подтверждён в стеме" не находила zero-padded "04" —
её anchored-к-началу-строки regex ожидал ведущий номер СРАЗУ в начале
имени файла ("04_Title"), а не после имени автора ("Далин 04 Title").
В результате том 4 получал sort_key=(0,1,0,0) — коллизия с настоящим
томом 1 ("Убить некроманта") — и серия из 9 файлов раскалывалась на
несвязанные группы "1-3"/"7-8" вместо одной "1-9".
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord


def _rec(path, num, title):
    return BookRecord(
        file_path=path, file_title=title, metadata_authors="Далин Макс",
        proposed_author="Далин Макс", author_source="folder_dataset",
        metadata_series="Мир Королей", proposed_series="Мир Королей",
        series_source="folder_dataset", series_number=num,
        series_number_source="metadata",
    )


class TestMetaNumConfirmedByMidStemZeroPaddedPrefix:
    def test_zero_padded_number_after_author_name_is_recognised(self):
        rec = _rec(
            "Далин 04 Костер и Саламандра. Книга первая.fb2", "4",
            "Костер и Саламандра. Книга 1",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        # Позиция В СЕРИИ обязана быть 4 (подтверждённый meta_num), а не 1
        # (roman_inline от "Книга 1", ошибочно принятого за позицию в серии).
        assert sort_key[1] == 4


class TestFullSeriesMergesIntoOneGroup:
    """Сквозная проверка на форме реального случая: все 9 файлов серии
    (включая внутренне разбитые "Костёр и Саламандра"/"Время неблагих")
    должны слиться в ОДНУ группу компиляции 1-9, а не расколоться на
    "1-3"/"7-8" с потерянными томами 4-6 и 9.
    """

    def test_nine_files_merge_into_single_group(self, tmp_path):
        from fb2parser_core.fb2_compiler import FB2CompilerService as _Svc

        titles = {
            1: "Убить некроманта",
            2: "Корона, огонь и медные крылья",
            3: "Моя Святая Земля",
            4: "Костер и Саламандра. Книга 1",
            5: "Костер и Саламандра. Книга 2",
            6: "Костер и Саламандра. Книга 3",
            7: "Фарфор Ее Величества",
            8: "Время неблагих. Книга 1",
            9: "Время неблагих. Книга 2",
        }
        records = [
            _rec(f"Далин 0{n} {titles[n].split('.')[0]}.fb2", str(n), titles[n])
            for n in range(1, 10)
        ]

        svc = _Svc()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Далин Макс"]
        assert len(matches) == 1, f"expected 1 merged group (1-9), got {len(matches)}"
        assert len(matches[0].books) == 9
