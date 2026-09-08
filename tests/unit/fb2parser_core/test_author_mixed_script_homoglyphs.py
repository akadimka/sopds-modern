"""Регрессия для `Pass3Normalize`/`_fix_mixed_script_homoglyphs()` — docs/
quality-roadmap.md, баг №42.

Реальный случай (замечен пользователем в превью компилятора): серия
"Анонимус" (18 томов) компилировалась в ТРИ разорванных куска (1-7,
9-12, 14-16), тома 8/13/17 не попадали НИКУДА, а том 18 терялся вовсе.
Причина — 15 из 18 файлов дают автора "Анонимyс" (латинская 'y' вместо
кириллической 'у', опечатка в метаданных FB2), а 3 файла (8, 13, 17) —
без опечатки, "Анонимус". `FB2CompilerService.find_groups()` группирует
книги по буквальному значению (автор, серия) — разное написание автора
считалось РАЗНЫМИ авторами.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass3_normalize import Pass3Normalize, _fix_mixed_script_homoglyphs


def _rec(author):
    return BookRecord(
        file_path="x.fb2", file_title="T", metadata_authors=author,
        proposed_author=author, author_source="metadata",
        metadata_series="", proposed_series="", series_source="", series_number="",
    )


class TestFixMixedScriptHomoglyphsUnit:
    def test_stray_latin_letter_in_cyrillic_word_replaced(self):
        assert _fix_mixed_script_homoglyphs("Анонимyс") == "Анонимус"

    def test_pure_cyrillic_word_untouched(self):
        assert _fix_mixed_script_homoglyphs("Анонимус") == "Анонимус"

    def test_pure_latin_word_untouched(self):
        # Настоящее иностранное имя/псевдоним на латинице не должно портиться.
        assert _fix_mixed_script_homoglyphs("Cameron Post") == "Cameron Post"

    def test_multi_word_author_only_affected_token_changed(self):
        assert _fix_mixed_script_homoglyphs("Ивaнов Петр") == "Иванов Петр"


class TestPass3NormalizeConvergesAuthorSpelling:
    def test_both_spellings_converge_to_same_author(self):
        records = [_rec("Анонимyс"), _rec("Анонимус")]
        Pass3Normalize(Logger()).execute(records)
        assert {r.proposed_author for r in records} == {"Анонимус"}
