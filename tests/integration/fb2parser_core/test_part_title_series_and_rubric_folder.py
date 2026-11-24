"""Найдено пользователем в `regen_Test1.csv`, папка «Акунин Борис -
Сборник\\История» (2026-11-24):

- «История» — папка-рубрика, как «Другие произведения»: её имя попадало в
  серию всем книгам папки («История») и корнем — подпапке
  («История\\История Российского государства»). Решение — общие данные:
  «История» в `series_folder_blacklist` (app_settings.json).
- «Б. Акунин - Любовь к истории (сетевая версия часть 3).fb2» — после
  исключения папки серия бралась из скобки имени файла: «Сетевая версия».
  Название в файле — «Любовь к истории (сетевая версия) ч.3»: тома
  различаются только номером части, серия — название без номера
  (`Pass2SeriesFilename._series_from_part_title`) в виде «Любовь к истории.
  Сетевая версия»: со скобкой в конце компилятор (группирует серии без
  скобки) слил бы её с бумажной серией «Любовь к истории» и для томов 1–4
  брал бы книги другой подборки.
"""
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

FOLDER = "Акунин Борис - Сборник\\История"


def _rec(path, title, **kw):
    kw.setdefault("metadata_series", "")
    kw.setdefault("proposed_series", "")
    kw.setdefault("series_source", "")
    return BookRecord(file_path=path, file_title=title, metadata_authors="Борис Акунин",
                      proposed_author="Акунин Борис", author_source="folder_dataset", **kw)


class TestSeriesFromPartTitle:
    def test_rule(self):
        f = Pass2SeriesFilename._series_from_part_title
        assert f("сетевая версия часть 3", "Любовь к истории (сетевая версия) ч.3") == \
            "Любовь к истории. Сетевая версия"
        # Кандидат без номера части или не в скобках названия — не применяется:
        # "Дашко Дмитрий. Мент 9. Ростов-папа 2.fb2", название «Мент. Ростов-папа
        # часть 2» — серия «Мент» из имени файла настоящая (подсерия Ростов-папа).
        assert f("Мент", "Мент. Ростов-папа часть 2") is None
        assert f("Мент часть 2", "Мент. Ростов-папа часть 2") is None
        # название без номера части — правило не применяется
        assert f("Звёздный путь часть 2", "Дорога домой") is None
        # кандидат не входит в название — не применяется
        assert f("Звёздный путь", "Дорога домой ч.2") is None
        assert f("", "Любовь к истории ч.1") is None

    def test_network_version_parts_get_titled_series(self):
        p = Pass2SeriesFilename(Logger(), config_path=_config_path())
        rec = _rec(f"{FOLDER}\\Б. Акунин - Любовь к истории (сетевая версия часть 3).fb2",
                   "Любовь к истории (сетевая версия) ч.3")
        p._process_single_record(rec, {})
        assert rec.proposed_series == "Любовь к истории. Сетевая версия"

    def test_series_in_brackets_without_part_title_unchanged(self):
        """Скобка с настоящей серией: название в файле без номера части —
        как раньше, серия из скобки."""
        p = Pass2SeriesFilename(Logger(), config_path=_config_path())
        rec = BookRecord(file_path="Иванов Иван\\Иванов Иван - Дорога домой (Звёздный путь часть 2).fb2",
                         file_title="Дорога домой", metadata_authors="Иван Иванов",
                         proposed_author="Иванов Иван", author_source="folder_dataset",
                         metadata_series="", proposed_series="", series_source="")
        p._process_single_record(rec, {})
        assert rec.proposed_series == "Звёздный путь"

    def test_flag_does_not_leak_to_next_book(self):
        p = Pass2SeriesFilename(Logger(), config_path=_config_path())
        first = _rec(f"{FOLDER}\\Б. Акунин - Любовь к истории (сетевая версия часть 1).fb2",
                     "Любовь к истории (сетевая версия) ч.1")
        second = BookRecord(file_path="Иванов Иван\\Иванов Иван - Дорога домой (Звёздный путь (ЛП)).fb2",
                            file_title="Дорога домой", metadata_authors="Иван Иванов",
                            proposed_author="Иванов Иван", author_source="folder_dataset",
                            metadata_series="", proposed_series="", series_source="")
        p._process_single_record(first, {})
        p._process_single_record(second, {})
        assert "(" not in second.proposed_series          # скобка у обычной серии срезается, как раньше


class TestHistoryIsRubricFolder:
    def _service(self, *records):
        service = RegenCSVService(_config_path())
        service.records = list(records)
        service._postcheck_series_folder_blacklist()
        return service.records

    def test_in_shared_blacklist(self):
        service = RegenCSVService(_config_path())
        assert "История" in service.settings.get_series_folder_blacklist()

    def test_folder_name_is_not_a_series(self):
        [rec] = self._service(_rec(f"{FOLDER}\\Б. Акунин - Любовь к истории (сборник исторических миниатюр).fb2",
                                   "Любовь к истории", proposed_series="История",
                                   series_source="folder_dataset"))
        assert rec.proposed_series == ""


_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<author><first-name>Борис</first-name><last-name>Акунин</last-name></author>
<book-title>{title}</book-title>{sequence}
</title-info></description>
<body><title><p>{title}</p></title><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _write(dir_, filename, title, seq=""):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / filename).write_text(_FB2.format(title=title, sequence=seq), encoding="utf-8")


class TestNetworkVersionCompiledApartFromPaperSeries:
    def test_two_compilation_groups(self, tmp_path):
        history = tmp_path / "Акунин Борис - Сборник" / "История"
        for n in (1, 2, 3):
            _write(history, f"Б. Акунин - Любовь к истории (сетевая версия часть {n}).fb2",
                   f"Любовь к истории (сетевая версия) ч.{n}")
        paper = history / "Б. Акунин - Серия Любовь к истории"
        for n, title in ((1, "Самый страшный злодей"), (2, "Настоящая принцесса")):
            _write(paper, f"0{n}. {title}.fb2", title,
                   f'<sequence name="Любовь к истории" number="{n}"/>')

        records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
        groups = {g.series: sorted(b.abs_path.name for b in g.books)
                  for g in FB2CompilerService().find_groups(records, tmp_path)}

        assert sorted(groups) == ["Любовь к истории", "Любовь к истории. Сетевая версия"]
        assert all("сетевая версия" in name for name in groups["Любовь к истории. Сетевая версия"])
        assert len(groups["Любовь к истории. Сетевая версия"]) == 3
        assert groups["Любовь к истории"] == ["01. Самый страшный злодей.fb2", "02. Настоящая принцесса.fb2"]
