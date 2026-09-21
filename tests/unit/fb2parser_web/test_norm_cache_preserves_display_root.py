"""Регрессия для `fb2parser_web.views._bookrecord_to_norm_dict()`/
`_rec_to_ns()` — docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (замечен пользователем): даже после того, как
`regen_csv.py` начал корректно заполнять `BookRecord.series_display_root`
("Мир Вальдиры"), экран предпросмотра компиляции всё равно показывал
голое "Кроу" без организационного корня — причём НЕ из-за stale-сервера
(перезапуск сервера и чистая проверка на реальных данных этого не
объяснили).

Настоящая причина: `_run_normalize_thread()` кэширует записи нормализации
в `norm_job`/на диск (`_norm_cache_save`, "переживёт перезапуск сервера")
как ПРОСТЫЕ dict через явный whitelist полей — `series_display_root` в
этом whitelist-е не было. Поле молча выпадало на этом шаге, ещё до того,
как `compiler_scan()` вызывает `find_groups()` — сам `regen_csv.py` был
уже полностью исправен, но веб-слой его результат обрезал.
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sopds.settings.local")
django.setup()

from types import SimpleNamespace

from fb2parser_web.views import _bookrecord_to_norm_dict, _rec_to_ns


def _fake_record(**overrides):
    base = dict(
        file_path="Мир Вальдиры\\Кроу (КРОУ)\\Кроу 2.fb2",
        file_title="Суровые земли",
        metadata_authors="Руслан Михайлов", proposed_author="Михайлов Руслан",
        author_source="folder_dataset", metadata_series="",
        proposed_series="Кроу", series_source="folder_dataset",
        series_number="2", series_number_source="filename_prefix",
        metadata_genre="sf_fantasy", series_display_root="Мир Вальдиры",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestBookrecordToNormDictKeepsDisplayRoot:
    def test_display_root_present_in_cached_dict(self):
        d = _bookrecord_to_norm_dict(_fake_record())
        assert d["series_display_root"] == "Мир Вальдиры"

    def test_missing_attribute_defaults_to_empty(self):
        rec = SimpleNamespace(file_path="x.fb2")  # без series_display_root вовсе
        d = _bookrecord_to_norm_dict(rec)
        assert d["series_display_root"] == ""


class TestRecToNsRoundTripsDisplayRoot:
    def test_dict_with_display_root_survives_round_trip(self):
        d = _bookrecord_to_norm_dict(_fake_record())
        ns = _rec_to_ns(d)
        assert ns.series_display_root == "Мир Вальдиры"

    def test_old_cache_without_key_defaults_to_empty(self):
        # Старый кэш на диске (до этого фикса) не содержит ключ вовсе —
        # не должно падать, должно дать пустую строку (баг №109, п.1: пусто ==
        # "нет организационного корня").
        old_style = {
            "file_path": "x.fb2", "proposed_series": "Кроу",
        }
        ns = _rec_to_ns(old_style)
        assert ns.series_display_root == ""
