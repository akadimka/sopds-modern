"""Регрессия для `Pass3SeriesNormalize.execute()` — обнаружено пользователем
на реальной библиотеке (docs/quality-roadmap.md, баг №27): unification "по
punct-нормализованному ключу" в конце пасса раньше группировала записи ПО
ВСЕЙ БИБЛИОТЕКЕ, без учёта автора. Punct-ключ схлопывает небуквенные
символы в пробел, так что "S-T-I-K-S" и "S-T-I-K-S #" дают ОДИН и тот же
ключ ("s t i k s") — решётка не входит в \\w.

Реальный случай: антология-вселенная "S-T-I-K-S" — десятки НИКАК не
связанных авторов пишут отдельные повести в общем сеттинге. У одного файла
("Лазарев Василий - S-T-I-K-S #9...") извлечение серии из имени файла
ошибочно роняло цифру, оставляя "S-T-I-K-S #". Библиотека-широкая
unification считала эту ОДНУ испорченную запись "тем же самым", что и
чистое "S-T-I-K-S" у ~15 ДРУГИХ, никак не связанных авторов — и как более
длинная строка (при равном приоритете источника "filename") побеждала как
канон, перетирая proposed_series всем им разом ("Галеев Эдуард", "Мушинский
Василий", "Рудкевич Ирэн" и т.д. все получали чужое "S-T-I-K-S #").

Фикс: группировка теперь по (author_norm, punct_key), а не по одному
punct_key — авторы с совпадающей по пунктуации серией не заражают друг
друга.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass3_series_normalize import Pass3SeriesNormalize
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author, series, series_source="filename"):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=author,
        proposed_author=author, author_source="filename",
        metadata_series="", proposed_series=series,
        series_source=series_source,
    )


class _NullLogger:
    def log(self, *args, **kwargs):
        pass


class TestPunctUnificationScopedByAuthor:
    def test_broken_value_from_one_author_does_not_poison_others(self):
        records = [
            # "Лазарев Василий" — испорченное значение (реальный случай).
            _rec("Лазарев Василий - S-T-I-K-S #9. И пришёл Лесник! 3.fb2",
                 "Лазарев Василий", "S-T-I-K-S #"),
            # Другие, никак не связанные авторы — чистое "S-T-I-K-S".
            _rec("Галеев Эдуард - S-T-I-K-S. Сварной-1.fb2",
                 "Галеев Эдуард", "S-T-I-K-S"),
            _rec("Мушинский Василий - S-T-I-K-S. Сломать Систему.fb2",
                 "Мушинский Василий", "S-T-I-K-S"),
            _rec("Рудкевич Ирэн - S-T-I-K-S. Шпилька.fb2",
                 "Рудкевич Ирэн", "S-T-I-K-S"),
        ]
        settings = SettingsManager(_config_path())
        pass3s = Pass3SeriesNormalize(_NullLogger(), settings=settings)
        pass3s.execute(records)

        assert records[1].proposed_series == "S-T-I-K-S"
        assert records[2].proposed_series == "S-T-I-K-S"
        assert records[3].proposed_series == "S-T-I-K-S"

    def test_same_author_still_gets_unified_by_punctuation(self):
        # Сама unification-логика (в рамках ОДНОГО автора) не должна ломаться:
        # два варианта одной серии, отличающиеся только пунктуацией, всё ещё
        # унифицируются к каноническому (более длинному) варианту.
        records = [
            _rec("Автор Тест - Ревизор. Возвращение в СССР 1.fb2",
                 "Автор Тест", "Ревизор. Возвращение в СССР"),
            _rec("Автор Тест - Ревизор возвращение в СССР 2.fb2",
                 "Автор Тест", "Ревизор возвращение в СССР"),
        ]
        settings = SettingsManager(_config_path())
        pass3s = Pass3SeriesNormalize(_NullLogger(), settings=settings)
        pass3s.execute(records)

        assert records[0].proposed_series == records[1].proposed_series
        assert records[0].proposed_series == "Ревизор. Возвращение в СССР"
