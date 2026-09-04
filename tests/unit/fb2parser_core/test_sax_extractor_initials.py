"""Регрессия для `FB2SAXExtractor` (fb2_sax_extractor.py) — обнаружено
через реальную серию "Пространство"/"The Expanse" (Абрахам Дэниел, Кори
Джеймс, docs/quality-roadmap.md, баг №17): в исходном FB2 у части книг
`<middle-name>` содержит слитные инициалы без пробела ("С.А." вместо
"С. А." или отсутствия инициалов вовсе) — такая склейка выглядит как ОДИН
непрерывный токен и последующие проходы (Pass 3 нормализация порядка слов,
Pass 4 консенсус по серии, Pass 5 author_surname_conversions) не
распознают её так же, как обычное написание — из-за чего 2 из 13 книг
одной и той же серии получали другого финального автора, чем остальные
11, и серия при полной сборке библиотеки распадалась на 2 отдельные
группы вместо одной.
"""
from pathlib import Path

from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author>
<first-name>Джеймс</first-name>
<middle-name>{middle}</middle-name>
<last-name>Кори</last-name>
</author>
<book-title>Тест</book-title>
</title-info>
</description>
<body><section><p>Text</p></section></body>
</FictionBook>
"""

_FB2_PSEUDONYM_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>sf</genre>
<author>
<first-name>N.B.</first-name>
<last-name></last-name>
</author>
<book-title>Тест</book-title>
</title-info>
</description>
<body><section><p>Text</p></section></body>
</FictionBook>
"""


def _write_fb2(tmp_path: Path, middle: str) -> Path:
    p = tmp_path / "book.fb2"
    p.write_text(_FB2_TEMPLATE.format(middle=middle), encoding="utf-8")
    return p


def _write_pseudonym_fb2(tmp_path: Path) -> Path:
    p = tmp_path / "book.fb2"
    p.write_text(_FB2_PSEUDONYM_TEMPLATE, encoding="utf-8")
    return p


class TestGluedInitialsGetSpaced:
    def test_extract_all_metadata_at_once_spaces_glued_initials(self, tmp_path):
        path = _write_fb2(tmp_path, "С.А.")
        extractor = FB2SAXExtractor(_config_path())
        meta = extractor._extract_all_metadata_at_once(path)
        assert meta["authors"] == "Джеймс С. А. Кори"

    def test_extract_metadata_with_sax_spaces_glued_initials(self, tmp_path):
        path = _write_fb2(tmp_path, "С.А.")
        extractor = FB2SAXExtractor(_config_path())
        authors_list, _series = extractor._extract_metadata_with_sax(path)
        assert authors_list == ["Джеймс С. А. Кори"]

    def test_already_spaced_initials_unchanged(self, tmp_path):
        path = _write_fb2(tmp_path, "С. А.")
        extractor = FB2SAXExtractor(_config_path())
        meta = extractor._extract_all_metadata_at_once(path)
        assert meta["authors"] == "Джеймс С. А. Кори"

    def test_plain_name_without_initials_unaffected(self, tmp_path):
        path = _write_fb2(tmp_path, "")
        extractor = FB2SAXExtractor(_config_path())
        meta = extractor._extract_all_metadata_at_once(path)
        assert meta["authors"] == "Джеймс Кори"


class TestStandaloneInitialsPseudonymUntouched:
    """Первая версия фикса раздвигала инициалы во ВСЕЙ объединённой строке
    имени автора, а не только в `<middle-name>` — из-за чего самостоятельный
    псевдоним-инициалы БЕЗ фамилии тоже задевался. Реальный случай: "N.B.
    ОЯШ 1-2.fb2" — <first-name>N.B.</first-name>, <last-name></last-name>
    пустой. Первая версия превращала "N.B." → "N. B." → downstream-проходы
    переставляли порядок слов как для настоящих имени+фамилии, получая
    испорченное "B. N." вместо исходного цельного псевдонима. Сузили фикс
    так, чтобы он трогал ТОЛЬКО поле middle_name — для одиночного
    first-name-психевдонима middle_name пуст, значит регэксп не применяется.
    """

    def test_standalone_initials_pseudonym_not_reordered(self, tmp_path):
        path = _write_pseudonym_fb2(tmp_path)
        extractor = FB2SAXExtractor(_config_path())
        meta = extractor._extract_all_metadata_at_once(path)
        assert meta["authors"] == "N.B."

    def test_standalone_initials_pseudonym_not_reordered_other_path(self, tmp_path):
        path = _write_pseudonym_fb2(tmp_path)
        extractor = FB2SAXExtractor(_config_path())
        authors_list, _series = extractor._extract_metadata_with_sax(path)
        assert authors_list == ["N.B."]
