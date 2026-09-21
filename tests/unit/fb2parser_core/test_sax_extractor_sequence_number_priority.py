"""Регрессия для `FB2SAXExtractor` — docs/quality-roadmap.md, баг №81.

Реальный случай (Зан Тимоти, "Траун. Доминация", FANZON): у книги 2 в
`<title-info>` идут `<sequence number="2" name="Траун. Доминация"/>`
ПЕРВЫМ, затем `<sequence name="Звёздные Войны"/>` (родовая франшиза, без
номера) — а у книги 3 порядок ОБРАТНЫЙ: `<sequence name="Звёздные
войны"/>` первым, `<sequence name="Траун. Доминация" number="3"/>`
вторым. Старое правило "последний тег в файле побеждает" (чистая
случайность порядка) давало РАЗНУЮ `metadata_series` для соседних томов
одной и той же серии — "Звёздные Войны" для книги 2, но правильную
"Траун. Доминация" для книги 3.
"""
from pathlib import Path

from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author><first-name>Тимоти</first-name><last-name>Зан</last-name></author>
<book-title>Тест</book-title>
{sequences}
</title-info>
</description>
<body><section><p>Text</p></section></body>
</FictionBook>
"""


def _write_fb2(tmp_path: Path, sequences: str) -> Path:
    p = tmp_path / "book.fb2"
    p.write_text(_FB2_TEMPLATE.format(sequences=sequences), encoding="utf-8")
    return p


class TestNumberedSequenceTakesPriorityOverOrder:
    def test_numbered_series_first_franchise_second(self, tmp_path):
        path = _write_fb2(
            tmp_path,
            '<sequence number="2" name="Траун. Доминация"/>'
            '<sequence name="Звёздные Войны"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Траун. Доминация"
        assert meta["series_number"] == "2"

    def test_franchise_first_numbered_series_second(self, tmp_path):
        path = _write_fb2(
            tmp_path,
            '<sequence name="Звёздные войны"/>'
            '<sequence number="3" name="Траун. Доминация"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Траун. Доминация"
        assert meta["series_number"] == "3"

    def test_no_number_anywhere_keeps_first_tag(self, tmp_path):
        # Sanity: если НИ у одного тега нет номера, побеждает первый —
        # не должно измениться поведение для книг без явной серии.
        path = _write_fb2(
            tmp_path,
            '<sequence name="Звёздные войны"/>'
            '<sequence name="Легенды"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Звёздные войны"


class TestZeroNumberedRootDoesNotOutrankRealSubseries:
    """Баг №110: реальный случай (Михайлов Руслан, «Мир Вальдиры\\Ведомости
    Бульквариуса\\Книга 1...fb2») — `<sequence name="Мир Вальдиры"
    number="0"/>` (организационный корень/вселенная автора, номер "0" —
    не настоящая позиция) идёт ПЕРВЫМ, затем `<sequence name="Ведомости
    Бульквариуса" number="1"/>` (настоящая подсерия с настоящим номером).

    Старая проверка бага №81 (`if seq_num: if not self.series_number:`)
    смотрит только "есть номер или нет" — и "0" тоже проходит как номер,
    поэтому первый тег (корень, "0") НАВСЕГДА занимает
    `self.series_number`, а второй тег (настоящая подсерия, "1") молча
    отбрасывается, хотя он и есть настоящий ответ. Итог до фикса:
    metadata_series="Мир Вальдиры", series_number="0" — оба неверны для
    этой книги.
    """

    def test_zero_numbered_root_first_real_subseries_second(self, tmp_path):
        path = _write_fb2(
            tmp_path,
            '<sequence name="Мир Вальдиры" number="0"/>'
            '<sequence name="Ведомости Бульквариуса" number="1"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Ведомости Бульквариуса"
        assert meta["series_number"] == "1"

    def test_zero_numbered_root_second_real_subseries_first(self, tmp_path):
        # Порядок тегов не должен иметь значения (как и в баге №81) —
        # проверяем оба направления.
        path = _write_fb2(
            tmp_path,
            '<sequence name="Ведомости Бульквариуса" number="1"/>'
            '<sequence name="Мир Вальдиры" number="0"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Ведомости Бульквариуса"
        assert meta["series_number"] == "1"

    def test_single_zero_numbered_sequence_kept_as_is(self, tmp_path):
        # Sanity: единственный тег с number="0" (без соседа с ненулевым
        # номером) — это не наш случай, менять его не нужно (может быть
        # легитимным "нулевым" томом/прологом).
        path = _write_fb2(tmp_path, '<sequence name="Мир Вальдиры" number="0"/>')
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Мир Вальдиры"
        assert meta["series_number"] == "0"

    def test_two_zero_numbered_sequences_keeps_first(self, tmp_path):
        # Sanity: если ОБА тега "number=0" — не с чем сравнивать, значит
        # это не признак "родовой корень против настоящей подсерии".
        # Оставляем прежнее поведение (первый побеждает).
        path = _write_fb2(
            tmp_path,
            '<sequence name="Мир Вальдиры" number="0"/>'
            '<sequence name="Другая ветка" number="0"/>',
        )
        meta = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(path)
        assert meta["series"] == "Мир Вальдиры"
        assert meta["series_number"] == "0"
