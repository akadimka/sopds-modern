"""Регрессия для `Pass4Consensus.execute()` — docs/quality-roadmap.md,
баг №66.

Реальный случай (замечен пользователем в компиляции): Клеванский Кирилл /
"Сердце Дракона" — 11 из 20 файлов серии несут `<sequence name="Сердце
Дракона. Нейросеть в мире боевых искусств">` в самих метаданных (заголовок
и подзаголовок склеены автором в одну строку через точку), а остальные —
голое "Сердце Дракона"/"Сердце дракона". "Folder metadata consensus"
(11 голосов против 9-10) присваивает ВСЕМ файлам склеенную строку, а идущий
следом второй "hierarchical series conversions (dot→backslash)" проход
безусловно резал любую строку вида "База. Хвост" (единственная проверка —
`subseries.isdigit()`) на "База\Хвост" — превращая подзаголовок в мнимую
подсерию "Нейросеть в мире боевых искусств". В результате серия
фрагментировалась на 2 несвязанные группы при компиляции — "Нейросеть..."
(11 файлов) отрывалась от остальных 9.

Ключевое отличие от настоящей подсерии: слово "Нейросеть" НИГДЕ не
встречается в именах самих файлов (только "Сердце Дракона. Том N") — это
чисто метаданный артефакт. Фикс требует, чтобы хвост после точки
дополнительно подтверждался именем ФАЙЛА (принцип "имя файла важнее
метаданных", уже используемый в этом модуле повсеместно), а не просто был
достаточно длинным/многословным — длина/число слов не отличают настоящую
подсерию от подзаголовка ("Нейросеть в мире боевых искусств" тоже длинная
и многословная).
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, meta_series, proposed_series, number, source="metadata"):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors="Кирилл Клеванский",
        proposed_author="Клеванский Кирилл", author_source="metadata",
        metadata_series=meta_series, proposed_series=proposed_series,
        series_source=source, series_number=number,
    )


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


class TestGluedSubtitleNotTreatedAsSubseries:
    def test_subtitle_absent_from_filenames_stays_flat(self):
        glued = "Сердце Дракона. Нейросеть в мире боевых искусств"
        records = [
            _rec(f"Клеванский {n:02d} Сердце Дракона. Том {n}.fb2", glued, glued, str(n))
            for n in range(2, 13)
        ] + [
            _rec(f"Клеванский {n:02d} Сердце Дракона. Том {n}.fb2",
                 "Сердце Дракона", "Сердце Дракона", str(n))
            for n in (1, 13, 15)
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert all(r.proposed_series == "Сердце Дракона" for r in records)
        assert all("\\" not in r.proposed_series for r in records)


class TestGenuineSubseriesConfirmedByFilenameStillConverted:
    def test_subseries_present_in_filename_becomes_hierarchical(self):
        # Sanity: если хвост ДЕЙСТВИТЕЛЬНО встречается в имени файла — это
        # похоже на настоящую подсерию, конверсия должна по-прежнему сработать.
        glued = "Рожденные в СССР. Личности"
        records = [
            _rec(f"Рожденные в СССР. Личности {n}.fb2", glued, glued, str(n))
            for n in (1, 2, 3)
        ] + [
            _rec("Рожденные в СССР.fb2", "Рожденные в СССР", "Рожденные в СССР", "1")
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        for r in records[:3]:
            assert r.proposed_series == "Рожденные в СССР\\Личности"
