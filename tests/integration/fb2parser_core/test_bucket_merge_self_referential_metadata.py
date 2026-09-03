"""Регрессия для `find_groups()`'s combined-bucket-merge metadata-подтверждения
(fb2_compiler.py) — обнаружено на реальной библиотеке пользователя
(Александра Лисина, docs/quality-roadmap.md, баг №11): короткая серия
«Времена» (10 книг) ошибочно сливалась с совершенно другой, самостоятельной
серией «Тёмные времена» (3 книги) только потому, что "времена" — последнее
слово в названии «Тёмные времена» самой по себе, и metadata_series одной из
книг «Тёмные времена» буквально повторяла имя её же собственной папки/серии
("Темные времена"). Такое самоподтверждение (metadata_series ДЛИННОГО бакета
== имя ЕГО ЖЕ бакета) не доказывает связи с короткой серией.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService


def _rec(path, author, series, metadata_series=""):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=author,
        proposed_author=author, author_source="metadata",
        proposed_series=series, series_source="folder_dataset",
        metadata_series=metadata_series,
    )


class TestSelfReferentialMetadataNotAcceptedAsConfirmation:
    AUTHOR = "Лисина Александра"

    def _records(self):
        vremena = [
            _rec(f"Времена\\{n:02d} Книга{n}.fb2", self.AUTHOR, "Времена")
            for n in range(1, 11)
        ]
        temnye_vremena = [
            _rec("Темные времена\\01 Враг.fb2", self.AUTHOR, "Темные времена",
                 metadata_series="Темные времена"),
            _rec("Темные времена\\02 Попутчик.fb2", self.AUTHOR, "Темные времена"),
            _rec("Темные времена\\03 Хозяин.fb2", self.AUTHOR, "Темные времена"),
        ]
        return vremena + temnye_vremena

    def test_unrelated_series_not_merged_by_self_referential_metadata(self, tmp_path):
        svc = FB2CompilerService()
        groups = svc.find_groups(self._records(), tmp_path)
        by_series = {g.series: g for g in groups}

        assert "Времена" in by_series, "серия «Времена» должна остаться отдельной группой"
        assert "Темные времена" in by_series, "серия «Темные времена» должна остаться отдельной группой"
        assert len(by_series["Времена"].books) == 10
        assert len(by_series["Темные времена"].books) == 3

        vremena_names = {b.abs_path.name for b in by_series["Времена"].books}
        temnye_names = {b.abs_path.name for b in by_series["Темные времена"].books}
        assert vremena_names.isdisjoint(temnye_names)
