"""Пары «псевдоним — настоящее имя» (docs/series-duplicates.md; сервер
2026-10-27: «Leach23» и «Михалек Дмитрий» — два автора в библиотеке).

Кнопка «Объединить» в отчёте «Дубли серий» пишет все написания автора →
выбранное в author_surname_conversions. Дальше это делает сам пайплайн:
книги из папки «Leach23» получают автора «Михалек Дмитрий», синхронизация
кладёт новые книги туда, а компилятор видит одного автора — сборник
«6-10» из папки псевдонима становится дублем нашей «Декалогии».
"""
import json
import shutil
from pathlib import Path

import pytest

from fb2parser_core.auto_compile_service import auto_compile_library
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path
from opds_catalog.series_check import alias_spellings, alias_variants
from tests.integration.fb2parser_core.test_third_party_omnibus import (
    SERIES,
    _omnibus,
    _our_compilation,
)

pytestmark = pytest.mark.django_db


def test_spelling_defaults_to_what_the_library_already_has():
    options, default = alias_spellings("Leach23", "Дмитрий Михалек", ["Leach23", "Михалек Дмитрий"])
    assert default == "Михалек Дмитрий"
    assert options == ["Михалек Дмитрий", "Leach23", "Leach23 (Михалек Дмитрий)"]
    # настоящего имени в библиотеке нет — по умолчанию папка псевдонима
    options, default = alias_spellings("Ascold Flow", "Мельник Андрей", ["Ascold Flow"])
    assert default == "Ascold Flow" and "Мельник Андрей" in options and "Андрей Мельник" in options
    assert alias_variants("Leach23", "Дмитрий Михалек", ["Leach23", "Михалек Дмитрий", "Другой Автор"]) == {
        "Leach23", "Дмитрий Михалек", "Михалек Дмитрий"}


@pytest.fixture
def settings_dir(tmp_path):
    """Каталог настроек с копией app_settings.json — замены имён пишутся туда."""
    real = Path(_config_path())
    folder = tmp_path / "settings"
    folder.mkdir()
    shutil.copy(real.parent / "app_settings.json", folder / "app_settings.json")
    (folder / "config.json").write_text("{}", encoding="utf-8")
    return folder


def _merge(settings_dir: Path, conversions: dict):
    app = settings_dir / "app_settings.json"
    data = json.loads(app.read_text(encoding="utf-8"))
    data.setdefault("author_surname_conversions", {}).update(conversions)
    app.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_merged_pseudonym_meets_the_compilation_on_sync(tmp_path, settings_dir):
    library = tmp_path / "library"
    alias_dir = library / "Фантастика" / "Leach23"
    real_dir = library / "Фантастика" / "Михалек Дмитрий"
    ours = _our_compilation(real_dir / SERIES / f"Михалек Дмитрий - {SERIES} (Декалогия).fb2", "Михалек Дмитрий", 10)
    part = _omnibus(alias_dir / f"Leach23 [Дмитрий Михалек]. {SERIES} 6-10.fb2",
                    f"Leach23 [Дмитрий Михалек]. {SERIES} 6-10", "Leach23", range(6, 11))
    config = str(settings_dir / "config.json")

    auto_compile_library(str(library), config_path=config, filter_paths={str(alias_dir), str(real_dir)})
    assert part.exists()                                    # до объединения — два разных автора

    _merge(settings_dir, {"Leach23": "Михалек Дмитрий", "Дмитрий Михалек": "Михалек Дмитрий"})
    records = RegenCSVService(config).generate_csv(str(library), output_csv_path=None)
    assert {r.proposed_author for r in records} == {"Михалек Дмитрий"}   # сюда синхронизация и положит книгу
    auto_compile_library(str(library), config_path=config, filter_paths={str(alias_dir), str(real_dir)})
    assert ours.exists() and not part.exists()
