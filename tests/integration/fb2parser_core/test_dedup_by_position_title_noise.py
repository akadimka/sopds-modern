"""Регрессия для FB2CompilerService._dedup_by_position(): хвостовые пометки
источника/издания в title ("[СИ]", "(ЛП)", "[litres]" и т.п.) не должны
мешать распознать дубль позиции тома, а из двух дублей должна оставаться
действительно более новая версия.

Обнаружено на реальной библиотеке (Иторр Кайл / "Зелёный луч"): один и тот
же роман "Золотая лихорадка" (том 3) лежал в двух версиях — издательской
("Золотая лихорадка (2018).fb2") и самиздатовской ("Золотая лихорадка [СИ]
(2015).fb2"). Дефекты нашлись сразу два:

1. `_dedup_by_position()` сравнивал title БЕЗ очистки от "[СИ]" — названия
   не совпадали буквально, поэтому обе версии считались "разными книгами,
   которым издатель присвоил один номер", и обе попадали в компиляцию как
   том 3, физически дублируя контент.
2. После фикса (1) обе версии верно распознавались как дубль, но из двух
   оставалась версия 2015 года, а не 2018-й. Причина в `_book_freshness()`:
   title-info `<date>` у обоих файлов означает год НАПИСАНИЯ произведения
   (2015 у обеих — корректно совпадает), но в файле-переиздании атрибут
   `<date value="2015-01-01">` заполнен точнее, чем в файле 2015 года
   (`<date value="">2015</date>`, значение только в тексте тега). Из-за
   этого `date_key` получал кортежи РАЗНОЙ длины — (-2015,) и
   (-2015,-1,-1) — и Python сравнивал их как -2015 < -2015 при равном
   префиксе: короткий кортеж оказывался "меньше" длинного, то есть менее
   точная запись ложно побеждала как "более свежая". Плюс отдельный баг —
   регэксп года из ИМЕНИ файла не распознавал "(2015)"/"(2018)" в скобках
   (ловил только "- 2018"/" 2018"), поэтому реальный год переиздания вообще
   не участвовал в сравнении, и решение проваливалось в финальный
   алфавитный tie-break, где "(2015)" лексикографически меньше "(2018)".
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService


def _book(path, sort_key, title=None):
    return CompilationBook(
        record=SimpleNamespace(
            file_title=title or Path(path).stem, proposed_series="Зелёный луч",
        ),
        abs_path=Path(path),
        sort_key=sort_key,
        sort_source="series_number",
        order_ambiguous=False,
        volume_label=str(sort_key[1]),
    )


class TestTitleNoiseStrippedBeforeDuplicateCheck:
    def test_si_tagged_duplicate_dropped(self):
        svc = FB2CompilerService()
        books = [
            _book("Золотая лихорадка (2018).fb2", (0, 3, 0, 0),
                  title="Золотая лихорадка"),
            _book("Золотая лихорадка [СИ] (2015).fb2", (0, 3, 0, 0),
                  title="Золотая лихорадка [СИ]"),
        ]
        duplicate_paths: list = []
        result = svc._dedup_by_position(books, duplicate_paths)

        assert len(result) == 1
        assert len(duplicate_paths) == 1

    def test_genuinely_different_books_at_same_position_both_kept(self):
        # Реальный случай (см. test_run_stats_duplicate_position.py) —
        # разные книги, издателем ошибочно пронумерованные одинаково,
        # по-прежнему не должны схлопываться в одну.
        svc = FB2CompilerService()
        books = [
            _book("09. Последний бог.fb2", (0, 9, 0, 0),
                  title="Последний бог"),
            _book("09. Проклятие королей.fb2", (0, 9, 0, 0),
                  title="Проклятие королей"),
        ]
        duplicate_paths: list = []
        result = svc._dedup_by_position(books, duplicate_paths)

        assert len(result) == 2
        assert duplicate_paths == []


def _write_fb2(path: Path, date_tag: str) -> None:
    path.write_bytes((
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook>\n'
        '<description><title-info>\n'
        '<book-title>Золотая лихорадка</book-title>\n'
        f'{date_tag}\n'
        '</title-info></description>\n'
        '<body><section><p>текст</p></section></body>\n'
        '</FictionBook>\n'
    ).encode('utf-8'))


class TestNewerReprintPreferredOverLessPreciseOlderDate:
    """Обе версии написаны в одном году (title-info date=2015 у обеих —
    год написания произведения не меняется от переиздания), но переиздание
    2018 года хранит дату точнее (value="2015-01-01" против пустого
    value=""), и его реальный год — в скобках в имени файла. Ни разница в
    точности записи одной и той же даты, ни формат "(YYYY)" в имени файла
    не должны мешать выбрать более новую версию.
    """

    def test_precise_and_bare_year_date_tags_treated_as_tied(self, tmp_path):
        older = tmp_path / "3. Золотая лихорадка (2015).fb2"
        newer = tmp_path / "3. Золотая лихорадка (2018).fb2"
        _write_fb2(older, '<date value="">2015</date>')
        _write_fb2(newer, '<date value="2015-01-01">2015</date>')

        svc = FB2CompilerService()
        books = [
            _book(str(newer), (0, 3, 0, 0), title="Золотая лихорадка"),
            _book(str(older), (0, 3, 0, 0), title="Золотая лихорадка [СИ]"),
        ]
        duplicate_paths: list = []
        result = svc._dedup_by_position(books, duplicate_paths)

        assert len(result) == 1
        assert result[0].abs_path == newer
        assert duplicate_paths == [older]
