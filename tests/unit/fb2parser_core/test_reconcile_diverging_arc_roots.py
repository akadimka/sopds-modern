"""Регрессия для `RegenCSVService._postcheck_reconcile_diverging_arc_roots()`.

Реальный случай (Вязовский Алексей / "Режим бога"): папка "С.К.С.,
Вязовский - Режим бога" не даёт серию через folder-извлечение (сама папка
одновременно матчится и как папка автора, и как название серии — глубже
неё подпапок нет, извлекать нечего). Каждый файл получает серию из СВОЕГО
имени файла, а имена в папке используют три РАЗНЫХ шаблона:

  "01-03"    → "С.К.С. - Режим бога. Книга N"        → "С.К.С.\\Режим бога"
  "04,05,08,09" → "Вязовский - Режим бога N. Подзаг." → "Вязовский\\Режим бога"
  "06,07,10,11,12" → тот же шаблон, но парсится плоско → "Режим бога"

Корень тут ("С.К.С." / "Вязовский") — не настоящая франшиза, а случайный
шум конкретного имени файла (то псевдоним, то фамилия, то ничего). Когда
одна и та же арка встречается у ОДНОГО автора под ≥2 разными корнями (или
под корнем и без него) — это доказательство, что корень не несёт
смысловой нагрузки: реальная серия — имя арки.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(series, author="Вязовский Алексей", title="T"):
    return BookRecord(
        file_path=f"{author} - {title}.fb2", file_title=title, metadata_authors=author,
        proposed_author=author, author_source="metadata",
        metadata_series="", proposed_series=series, series_source="filename",
        series_number="1", series_number_source="filename",
    )


def _run(records):
    service = RegenCSVService(_config_path())
    service.records = records
    service._postcheck_reconcile_diverging_arc_roots()
    return records


class TestDivergingRootsReconciledToFlatArc:
    def test_three_way_split_unified_to_flat(self):
        recs = [
            _rec("С.К.С.\\Режим бога"),
            _rec("С.К.С.\\Режим бога"),
            _rec("Вязовский\\Режим бога"),
            _rec("Вязовский\\Режим бога"),
            _rec("Режим бога"),
            _rec("Режим бога"),
        ]
        _run(recs)
        assert all(r.proposed_series == "Режим бога" for r in recs)
        # Иерархические записи получили новый source; уже-плоские (совпали
        # с целевым видом сразу) — не тронуты, source остаётся исходным.
        assert all(r.series_source == "arc_root_reconciled" for r in recs[:4])
        assert all(r.series_source == "filename" for r in recs[4:])

    def test_two_different_hierarchical_roots_unified(self):
        # Без плоской формы вообще — берём имя арки из первой иерархической.
        recs = [
            _rec("С.К.С.\\Режим бога"),
            _rec("Вязовский\\Режим бога"),
        ]
        _run(recs)
        assert all(r.proposed_series == "Режим бога" for r in recs)


class TestSingleRootFormLeftUntouched:
    def test_only_one_root_variant_not_touched(self):
        # Все записи под ОДНИМ и тем же корнем — никакого расхождения,
        # может быть настоящей осмысленной франшизой/подсерией.
        recs = [
            _rec("Отряд Сигма\\Такер Уэйн"),
            _rec("Отряд Сигма\\Такер Уэйн"),
        ]
        _run(recs)
        assert all(r.proposed_series == "Отряд Сигма\\Такер Уэйн" for r in recs)

    def test_single_flat_series_not_touched(self):
        recs = [_rec("Режим бога")]
        _run(recs)
        assert recs[0].proposed_series == "Режим бога"


class TestDifferentAuthorsNotMerged:
    def test_same_arc_name_different_authors_not_merged(self):
        recs = [
            _rec("С.К.С.\\Режим бога", author="Автор Один"),
            _rec("Режим бога", author="Автор Два"),
        ]
        _run(recs)
        assert recs[0].proposed_series == "С.К.С.\\Режим бога"
        assert recs[1].proposed_series == "Режим бога"


class TestShortArcNameNotMerged:
    def test_short_arc_name_skipped(self):
        # Арка короче 4 символов после нормализации — риск случайного
        # совпадения слишком велик, не рискуем сливать.
        recs = [
            _rec("Корень1\\Топ"),
            _rec("Корень2\\Топ"),
        ]
        _run(recs)
        assert recs[0].proposed_series == "Корень1\\Топ"
        assert recs[1].proposed_series == "Корень2\\Топ"
