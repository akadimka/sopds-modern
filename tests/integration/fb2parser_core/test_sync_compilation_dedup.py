"""Регрессия для `SynchronizationService._deduplicate_by_compilation()` и
`_build_target_filename()` (synchronization.py) — обнаружено на реальной
группе "Лисина Александра / Артур Рэйш" (docs/quality-roadmap.md, баг №12).

Sync-time дедупликация раньше умела удалять только ОДИНОЧНЫЕ тома,
покрытые компиляцией — сами compilation-записи всегда переезжали в
библиотеку КАЖДАЯ отдельным файлом, даже если один диапазон полностью
покрывал другой (напр. "1-2"+"3-4" рядом с уже готовым "1-4"): контент
физически дублировался, а `_build_target_filename` вдобавок штамповал
каждому фрагменту свой ЛОКАЛЬНЫЙ суффикс ("Дилогия", "т. 3-4" и т.п.),
теряя из имени файла числовой диапазон, необходимый последующему
auto-compile — из-за чего финальный скомпилированный файл получал
неверное количество томов в суффиксе.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _rec(path, author="Автор Тест", series="Серия Тест", series_number="", title=""):
    return BookRecord(
        file_path=path, file_title=title or path, metadata_authors=author,
        proposed_author=author, author_source="metadata",
        proposed_series=series, series_source="folder_dataset",
        metadata_series="", series_number=series_number,
    )


def _sync():
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.log_callback = None
    svc._log = lambda msg: None
    return svc


class TestConfidentCompilationRedundancyDropped:
    """Артур Рэйш: "1-2" и "3-4" (явный числовой диапазон в series_number)
    полностью покрыты уже готовым файлом "1-4" — физически дублирующие
    фрагменты должны удаляться, а не переезжать в библиотеку отдельно.
    """

    def _records(self):
        return [
            _rec("01-02.fb2", series_number="1-2"),
            _rec("03-04.fb2", series_number="3-4"),
            _rec("Серия Тест (Сборник, кн. 1-4).fb2", title="Серия Тест (1-4)"),
            _rec("05-06.fb2", series_number="5-6"),
        ]

    def test_redundant_ranges_deleted(self):
        kept, deleted = _sync()._deduplicate_by_compilation(self._records(), None)
        deleted_names = {r.file_path for r in deleted}
        kept_names = {r.file_path for r in kept}
        assert deleted_names == {"01-02.fb2", "03-04.fb2"}
        assert kept_names == {"Серия Тест (Сборник, кн. 1-4).fb2", "05-06.fb2"}

    def test_kept_compilation_keeps_original_filename(self):
        kept, _ = _sync()._deduplicate_by_compilation(self._records(), None)
        sync = _sync()
        for rec in kept:
            kind, vols, _conf = sync._classify_record(rec)
            assert kind == "compilation"
            target_name = sync._build_target_filename(rec, kind, vols)
            # Оригинальное имя (с числовым диапазоном) сохранено — не
            # заменено на локальный, потенциально вводящий в заблуждение
            # суффикс вида "Дилогия"/"т. 5-6".
            assert target_name.endswith(rec.file_path)


class TestGuessedRangeCompilationsNeverConsideredRedundant:
    """"Волжане" (Архипов Андрей): три РАЗНЫХ физических файла одной серии
    ("Поветлужье (трилогия)", "Волжане (Волжане. Трилогия)" с явным
    series_number="1-3", "Цикл «Волжане»") — два из них классифицируются
    только по ключевому слову "трилогия" (без реального числового
    подтверждения диапазона). Совпадение guessed-диапазона ({1,2,3}) НЕ
    доказывает, что это один и тот же контент — такие записи не должны
    ни удаляться, ни использоваться как основание для удаления других.
    """

    def _records(self):
        return [
            _rec("Поветлужье (трилогия).fb2", series_number="",
                 title="Поветлужье (трилогия)"),
            _rec("Волжане (Волжане. Трилогия).fb2", series_number="1-3",
                 title="Волжане (сборник)"),
            _rec("Цикл «Волжане».fb2", series_number="", title="Волжане (трилогия)"),
        ]

    def test_no_guessed_range_compilation_deleted(self):
        kept, deleted = _sync()._deduplicate_by_compilation(self._records(), None)
        assert deleted == []
        assert {r.file_path for r in kept} == {
            "Поветлужье (трилогия).fb2",
            "Волжане (Волжане. Трилогия).fb2",
            "Цикл «Волжане».fb2",
        }
