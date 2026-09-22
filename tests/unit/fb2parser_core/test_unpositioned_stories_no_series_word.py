"""Регрессия для `FB2CompilerService._series_suffix()` — docs/quality-
roadmap.md, баг №45.

Реальный случай (замечен пользователем в превью компилятора): Белаш
Александр / "Рассказы" — 7 самостоятельных рассказов, ни у одного нет
номера тома (`series_number="0"` у всех — placeholder, не реальная
позиция). Суффикс получался "Гепталогия" — слово, подразумевающее
последовательную 7-томную серию, хотя рассказы никак не связаны по
порядку. `_series_suffix()` трактовала `lo == 0` наравне с `lo == 1`
(настоящий старт нумерации с первого тома) — но `lo == hi == 0` при
`n_volumes > 1` на самом деле означает, что НИ У ОДНОЙ книги нет
настоящей позиции (`_run_stats()` считает каждую такую книгу отдельным
"виртуальным" томом через уникальный отрицательный id именно потому,
что реальной позиции нет).
"""
from fb2parser_core.fb2_compiler import FB2CompilerService


class TestFakeZeroRangeGetsPlainBookCount:
    def test_all_unpositioned_books_get_plain_count_not_series_word(self):
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=7, lo=0, hi=0, part_count=0, series_complete=True)
        assert suffix == "в 7 книгах"
        assert "Гепталогия" not in suffix

    def test_single_unpositioned_book(self):
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=1, lo=0, hi=0, part_count=0, series_complete=True)
        assert suffix == "в 1 книге"

    def test_real_series_starting_at_volume_1_still_gets_series_word(self):
        # Sanity: настоящая серия lo=1 (реальная позиция 1) по-прежнему
        # получает словесную форму — фикс не сломал обычный случай.
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=3, lo=1, hi=3, part_count=0, series_complete=True)
        assert suffix == "Трилогия"

    def test_genuine_single_volume_zero_still_uses_series_word_path(self):
        # Sanity: n_volumes==1 при lo=hi=0 — это НЕ "фейковый" случай (нет
        # противоречия n_volumes vs диапазон), это одна книга без позиции —
        # должно давать "в 1 книге" через обычную ветку, не через
        # _fake_zero_range (для n_volumes==1 обе ветки совпадают, но это
        # подтверждает отсутствие искусственного различия в этом крае).
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=1, lo=0, hi=0, part_count=0, series_complete=True)
        assert suffix == "в 1 книге"


class TestPrequelAtZeroDoesNotInflateMainCycleWord:
    """Реальный случай (замечен пользователем в превью компилятора): Пехов
    Алексей / "Хроники Сиалы" — "0. Змейка.fb2" (самостоятельная повесть-
    приквел, позиция "0") + "Хроники Сиалы. Трилогия.fb2" (настоящий,
    уже собранный омнибус трилогии, покрывает 1-3). Суффикс получался
    "Тетралогия" (4 позиции: 0,1,2,3) — хотя приквел не часть основной
    пронумерованной последовательности, а самостоятельное произведение.
    Пользователь подтвердил: считать N-логию по ГЛАВНОМУ циклу (1..hi),
    не включая позицию 0 в счёт — содержимое позиции 0 всё равно попадает
    в файл, меняется только подпись.
    """

    def test_prequel_plus_trilogy_labeled_as_trilogy_not_tetralogy(self):
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=4, lo=0, hi=3, part_count=0, series_complete=True)
        assert suffix == "Трилогия"

    def test_prequel_plus_single_main_book_falls_back_to_plain_count(self):
        # Sanity: приквел + всего 1 настоящий том — словесной формы для
        # "1" нет (SERIES_WORDS[1] отсутствует), считаем все физические
        # книги как есть.
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=2, lo=0, hi=1, part_count=0, series_complete=True)
        assert suffix == "в 2 книгах"

    def test_real_zero_to_zero_range_unaffected(self):
        # Sanity: lo==hi==0 (баг №45, "фейковый" диапазон — ни у кого нет
        # позиции) — эта ветка отдельная и не тронута данным фиксом.
        svc = FB2CompilerService()
        suffix = svc._series_suffix(n_volumes=7, lo=0, hi=0, part_count=0, series_complete=True)
        assert suffix == "в 7 книгах"
