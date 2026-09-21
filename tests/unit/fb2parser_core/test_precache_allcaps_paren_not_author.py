"""Регрессия для `Precache.execute()` (`_paren_surname` heuristic,
precache.py) — docs/quality-roadmap.md, баг №109 ("Причина A").

Реальный случай (Михайлов Руслан, "Мир Вальдиры\\Кроу (КРОУ)",
"...\\Господство клана Неспящих (ГКН)", "...\\Сточные Воды Альгоры
(СВА)") — регекс `_paren_surname`, задуманный для распознавания
папок-серий вида "Воин Грёзы (Широков)" (одна кириллическая ФАМИЛИЯ в
скобках, Title Case), по ошибке также матчил ЗАГЛАВНЫЕ АББРЕВИАТУРЫ в
скобках ("КРОУ", "ГКН", "СВА") — символьный класс `[а-яёА-ЯЁ\\-]{2,}`
для "хвоста" слова допускал заглавные буквы, а не только строчные, так
что "КРОУ" проходил ту же проверку, что и "ироков" в "Широков".

Итог: "Кроу (КРОУ)" кэшировался как АВТОРСКАЯ папка (`[CACHE] Added
HIGH: Кроу (КРОУ) → 'КРОУ'`), из-за чего
`_postcheck_build_subfolder_hierarchy()` (regen_csv.py) пропускал её
целиком (защита "родитель не должен быть авторской папкой") — она не
получала ни иерархию, ни `series_display_root` при синхронизации,
хотя физически лежит в той же организационной папке "Мир Вальдиры",
что и "Герой крайних рубежей"/"Цикл Люца" (которые эту папку получают).

Фикс: аббревиатура (весь захваченный текст в скобках — ЗАГЛАВНЫЕ буквы
без единой строчной) больше не считается фамилией.
"""
from pathlib import Path

from fb2parser_core.logger import Logger
from fb2parser_core.precache import Precache
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Руслан</first-name><last-name>Михайлов</last-name></author>
<book-title>Тест</book-title></title-info></description>
<body><section><p>Текст</p></section></body></FictionBook>
"""


def _write(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2, encoding="utf-8")


def _precache(work_dir: Path) -> Precache:
    return Precache(work_dir, SettingsManager(_config_path()), Logger(), folder_parse_limit=10)


class TestAllCapsAbbreviationInParensNotTreatedAsAuthor:
    def test_kroy_abbreviation_not_cached_as_author(self, tmp_path):
        author_dir = tmp_path / "Михайлов Руслан - Сборник"
        series_dir = author_dir / "Мир Вальдиры" / "Кроу (КРОУ)"
        _write(series_dir / "Кроу 3. Азы мастерства.fb2")

        precache = _precache(tmp_path)
        cache = precache.execute()

        assert series_dir not in cache

    def test_gkn_abbreviation_not_cached_as_author(self, tmp_path):
        author_dir = tmp_path / "Михайлов Руслан - Сборник"
        series_dir = author_dir / "Мир Вальдиры" / "Господство клана Неспящих (ГКН)"
        _write(series_dir / "Мир Вальдиры 4. Гром небесный.fb2")

        precache = _precache(tmp_path)
        cache = precache.execute()

        assert series_dir not in cache

    def test_real_titlecase_surname_in_parens_still_cached_as_author(self, tmp_path):
        # Sanity: настоящая фамилия в скобках (Title Case) — исходное
        # назначение регекса — не должна перестать распознаваться.
        series_dir = tmp_path / "Воин Грёзы (Широков)"
        _write(series_dir / "Книга 1.fb2")

        precache = _precache(tmp_path)
        cache = precache.execute()

        assert series_dir in cache
        assert cache[series_dir][0] == "Широков"
