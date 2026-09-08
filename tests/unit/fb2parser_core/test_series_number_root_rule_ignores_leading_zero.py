"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 2, «SeriesRoot N. BookTitle») — docs/quality-roadmap.md, баг №41.

Реальный случай (та же серия "Анонимус", том 8): имя файла "Анонимус 08 -
Дело наследника цесаревича.fb2" содержит номер с ведущим нулём ("08"), а
`series_number` из метаданных (`<sequence number="8"/>`) — без ведущего
нуля ("8"). Правило 2 сравнивало эти строки БУКВАЛЬНО ("8" != "08") и
считало это несовпадением — перезаписывало верный series_number="8"
(source='metadata') на "08" (source='filename_series_root'), хотя это
одно и то же значение, отличающееся только форматированием имени файла.
Из-за этого более поздний, более авторитетный источник (например,
исключение для псевдоним-серий из бага №40, которое смотрит на
`series_number_source == 'metadata'`) переставал видеть метаданные как
источник и откатывался на неверное поведение.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, proposed_series, series_number):
    return BookRecord(
        file_path=file_path, file_title="Дело наследника цесаревича",
        metadata_authors="Анонимус", proposed_author="Анонимус", author_source="metadata",
        metadata_series="Анонимус", proposed_series=proposed_series,
        series_source="metadata", series_number=series_number,
        series_number_source="metadata",
    )


class TestLeadingZeroDoesNotOverrideMetadataNumber:
    def test_zero_padded_filename_number_kept_as_metadata_source(self):
        rec = _rec("Анонимус 08 - Дело наследника цесаревича.fb2", "Анонимус", "8")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "8"
        assert rec.series_number_source == "metadata"

    def test_genuinely_different_number_still_corrected(self):
        # Sanity: реально РАЗНЫЙ номер (не просто padding) по-прежнему
        # перезаписывается — правило не сломано целиком.
        rec = _rec("Анонимус 09 - Гибель Сатурна.fb2", "Анонимус", "8")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"
        assert rec.series_number_source == "filename_series_root"
