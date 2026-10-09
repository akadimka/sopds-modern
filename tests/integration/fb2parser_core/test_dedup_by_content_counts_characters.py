"""Регрессия для FB2CompilerService._dedup_by_content(): объём книги считается
в СИМВОЛАХ текста, а не в байтах файла.

Обнаружено на реальной библиотеке (Большаков Валерий / «Закон меча», том 1):
издание 2008 года лежало в windows-1251, переиздание 2016 года — в UTF-8.
Начало текста совпадало (это одна книга), и из пары оставался «больший»
файл — но размер мерился в байтах, а русская буква в UTF-8 занимает 2 байта
против 1 в windows-1251. Выигрывала кодировка, а не содержимое:
- издание в 1251 с дополнительной повестью проиграло бы более короткому
  изданию в UTF-8 и удалилось бы вместе с повестью;
- защита «проигравший заметно (в 1,6 раза) крупнее победителя — вероятно,
  нераспознанный сборник, не удалять» ложно срабатывала на одном и том же
  тексте в двух кодировках (1,9×), и настоящий дубль оставался в сборнике.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService

_OPENING = ("Россия, Санкт-Петербург. 2007 год. Отточенная стрела чиркнула Олегу по плечу, "
            "располосовав кожу и пустив кровь. Пустяки, дело житейское. ") * 20
_NOVEL = "Олег шёл по набережной и думал о прошлом, о мечах и о дальних походах. " * 400
_NOVELLA = "Дополнительная повесть о том, как варяги ходили в Царьград за славой. " * 300

_FB2 = """<?xml version="1.0" encoding="{enc}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><book-title>{title}</book-title></title-info></description>
<body><section><title><p>Глава 1</p></title><p>{text}</p></section></body>
</FictionBook>
"""


def _write(path: Path, enc: str, title: str, text: str) -> Path:
    path.write_bytes(_FB2.format(enc=enc, title=title, text=text).encode(enc))
    return path


def _book(path: Path, sort_key):
    return CompilationBook(
        record=SimpleNamespace(file_title=path.stem, proposed_series="Закон меча"),
        abs_path=path, sort_key=sort_key, sort_source="series_number",
        order_ambiguous=False, volume_label=str(sort_key[1]),
    )


def test_longer_cp1251_edition_beats_shorter_utf8_one(tmp_path):
    # больше текста (роман + повесть), но меньше байт — 1251
    full = _write(tmp_path / "01. Закон меча.fb2", "windows-1251", "Закон меча", _OPENING + _NOVEL + _NOVELLA)
    # только роман, но больше байт — UTF-8
    short = _write(tmp_path / "01. Меч Вещего Олега.fb2", "utf-8", "Меч Вещего Олега", _OPENING + _NOVEL)
    assert full.stat().st_size < short.stat().st_size  # байты обманули бы

    duplicate_paths: list = []
    kept = FB2CompilerService()._dedup_by_content([_book(full, (0, 1, 0, 0)), _book(short, (0, 1, 0, 0))],
                                                  duplicate_paths)

    assert [b.abs_path for b in kept] == [full]
    assert duplicate_paths == [short]


def test_same_text_in_two_encodings_is_still_a_duplicate(tmp_path):
    # Победитель по точности позиции — в 1251, проигравший — тот же текст в UTF-8:
    # «крупнее в 1,6 раза» по байтам, но не по символам — это дубль, а не сборник.
    precise = _write(tmp_path / "01. Закон меча.fb2", "windows-1251", "Закон меча", _OPENING + _NOVEL)
    loose = _write(tmp_path / "Закон меча (переиздание).fb2", "utf-8", "Закон меча", _OPENING + _NOVEL)
    assert loose.stat().st_size > precise.stat().st_size * 1.6

    duplicate_paths: list = []
    kept = FB2CompilerService()._dedup_by_content([_book(precise, (0, 1, 0, 0)), _book(loose, (0, 0, 1, 0))],
                                                  duplicate_paths)

    assert [b.abs_path for b in kept] == [precise]
    assert duplicate_paths == [loose]


_IMG = '<binary id="i{n}.jpg" content-type="image/jpeg">' + "QUFBQQ==" * 50 + "</binary>"


def _write_illustrated(path: Path, enc: str, text: str, images: int) -> Path:
    body = _FB2.format(enc=enc, title=path.stem, text=text).replace(
        "</FictionBook>", "".join(_IMG.format(n=i) for i in range(images)) + "</FictionBook>")
    path.write_bytes(body.encode(enc))
    return path


def test_illustrated_edition_wins_when_text_is_the_same(tmp_path):
    # Безбашенный, «Античная наркомафия-8 (иллюстр)»: 226 иллюстраций при том же
    # тексте — раньше оставалось лишь случайно (UTF-8 против 1251 по байтам).
    plain = _write_illustrated(tmp_path / "08_Наркомафия-8.fb2", "windows-1251", _OPENING + _NOVEL + "Ещё фраза.", 1)
    illustrated = _write_illustrated(tmp_path / "08_Наркомафия-8 (иллюстр).fb2", "utf-8", _OPENING + _NOVEL, 12)

    duplicate_paths: list = []
    kept = FB2CompilerService()._dedup_by_content([_book(plain, (0, 8, 0, 0)), _book(illustrated, (0, 8, 0, 0))],
                                                  duplicate_paths)

    assert [b.abs_path for b in kept] == [illustrated] and duplicate_paths == [plain]


def test_more_text_beats_illustrations_when_texts_differ(tmp_path):
    full = _write_illustrated(tmp_path / "01. Полное.fb2", "windows-1251", _OPENING + _NOVEL + _NOVELLA, 0)
    illustrated = _write_illustrated(tmp_path / "01. С картинками.fb2", "utf-8", _OPENING + _NOVEL, 30)

    duplicate_paths: list = []
    kept = FB2CompilerService()._dedup_by_content([_book(full, (0, 1, 0, 0)), _book(illustrated, (0, 1, 0, 0))],
                                                  duplicate_paths)

    assert [b.abs_path for b in kept] == [full]
