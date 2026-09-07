"""Регрессия для `Pass3Normalize.execute()` (pass3_normalize.py) —
обнаружено пользователем в regen CSV на реальной группе Александр
Айзенберг / "Александр Берг" (серия "Антиблицкриг"): `proposed_author`
оставался в порядке "Александр Берг" (Имя Фамилия) вместо правильного
"Берг Александр" (Фамилия Имя), хотя `author_surname_conversions`
(app_settings.json) уже содержит "Айзенберг": "Берг" и папка/метаданные
дают достаточно информации для нормализации.

Причина: `normalize_format()` корректно переставляет "Александр Берг" →
"Берг Александр", но защитная эвристика (введённая ради других реальных
случаев вроде "Линдквист Йон Айвиде") откатывает любую перестановку,
если сырые СЛОВА `metadata_authors` её не подтверждают. Метаданные хранят
настоящую фамилию автора ("Александр Айзенберг"), а не псевдоним — сырое
слово "айзенберг" никогда не совпадает с "берг", подтверждение не
проходит, реордер откатывается.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass3_normalize import Pass3Normalize
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


class _NullLogger:
    def log(self, *args, **kwargs):
        pass


def _rec(proposed_author, metadata_authors, author_source="folder_dataset"):
    return BookRecord(
        file_path="x.fb2", file_title="Название",
        metadata_authors=metadata_authors, proposed_author=proposed_author,
        author_source=author_source, metadata_series="", proposed_series="",
        series_source="",
    )


class TestPseudonymSurnameConversionStillAllowsReorder:
    def test_folder_dataset_pseudonym_author_reordered_to_surname_first(self):
        rec = _rec("Александр Берг", "Александр Айзенберг")
        settings = SettingsManager(_config_path())
        pass3 = Pass3Normalize(logger=_NullLogger(), settings=settings)
        pass3.execute([rec])
        assert rec.proposed_author == "Берг Александр"

    def test_regular_folder_dataset_reorder_still_works(self):
        # Не псевдоним — sanity check, что фикс не сломал обычный случай:
        # метаданные подтверждают перестановку напрямую сырыми словами.
        rec = _rec("Александр Тамоников", "Александр Александрович Тамоников")
        settings = SettingsManager(_config_path())
        pass3 = Pass3Normalize(logger=_NullLogger(), settings=settings)
        pass3.execute([rec])
        assert rec.proposed_author == "Тамоников Александр"
