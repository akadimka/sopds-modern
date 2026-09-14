"""Регрессия для `Pass4Consensus.execute()` (унификация автора по серии с
общим соавтором) — docs/quality-roadmap.md, баг №72.

Реальный случай (замечен пользователем в превью компилятора): Барчук
Павел, Ларин Павел / "ОБХСС" — трилогия физически задублирована в 4
папках, по одной на каждого соавтора отдельно ("Барчук Павел\ОБХСС..." и
"Ларин Павел\ОБХСС..."). `author_source=folder_dataset` режет
`<author>`-метаданные до ОДНОГО имени — имени папки, хотя метаданные
КАЖДОГО файла (без исключения, без межфайлового сравнения) уже
согласованно перечисляют ОБОИХ соавторов. Существующая унификация автора
по серии с общим токеном ("Барчук Павел" ⊂ "Барчук Павел, Прядеев
Евгений") отказывалась трогать `folder_dataset`-записи вовсе — и не
умела ОБЪЕДИНЯТЬ два РАВНОЗНАЧНЫХ по числу токенов варианта
("Барчук Павел" и "Ларин Павел", 2 токена каждый, не подмножество друг
друга) в общий "Барчук Павел, Ларин Павел".
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author, meta_authors):
    return BookRecord(
        file_path=path, file_title=path, metadata_authors=meta_authors,
        proposed_author=author, author_source="folder_dataset",
        metadata_series="ОБХСС", proposed_series="ОБХСС", series_source="folder_dataset",
        series_number="1",
    )


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


class TestFolderDatasetAuthorWidenedByOwnMetadataConfirmation:
    def test_peer_single_names_merged_when_own_metadata_confirms_both(self):
        _META = "Павел Барчук; Павел Ларин"
        records = [
            _rec(r"Барчук Павел\ОБХСС\01.fb2", "Барчук Павел", _META),
            _rec(r"Барчук Павел\ОБХСС\02.fb2", "Барчук Павел", _META),
            _rec(r"Ларин Павел\ОБХСС\01.fb2", "Ларин Павел", _META),
            _rec(r"Ларин Павел\ОБХСС\02.fb2", "Ларин Павел", _META),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert all(r.proposed_author == "Барчук Павел, Ларин Павел" for r in records)

    def test_single_name_not_widened_without_own_metadata_confirmation(self):
        # Sanity: если у КОНКРЕТНОЙ записи метаданные НЕ подтверждают
        # объединённую форму (например, реальный разнобой — где-то в
        # серии метаданные другого автора вообще без Ларина), эта запись
        # не должна расширяться на основании чужих файлов.
        records = [
            _rec(r"Барчук Павел\ОБХСС\01.fb2", "Барчук Павел", "Павел Барчук"),
            _rec(r"Ларин Павел\ОБХСС\01.fb2", "Ларин Павел", "Павел Барчук; Павел Ларин"),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[0].proposed_author == "Барчук Павел"

    def test_widened_even_without_a_second_copy_under_the_other_coauthor(self):
        # Реальный случай — "СМЕРШ" (Барчук, Ларин): существует только под
        # ОДНОЙ папкой автора, нет копии под именем второго соавтора — во
        # всём бакете серии единственный proposed_author = "Барчук Павел"
        # (нет расхождения вариантов вовсе). Метаданные ОБОИХ файлов
        # (100% большинство) уже согласованно говорят про соавторство —
        # этого достаточно, кросс-папочный дубль не нужен.
        _META = "Павел Барчук; Павел Ларин"
        records = [
            _rec(r"Барчук Павел\СМЕРШ\01.fb2", "Барчук Павел", _META),
            _rec(r"Барчук Павел\СМЕРШ\02.fb2", "Барчук Павел", _META),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert all(r.proposed_author == "Барчук Павел, Ларин Павел" for r in records)

    def test_middle_name_in_metadata_dropped_not_left_unreordered(self):
        # Реальный случай — "Завод" (Гуров, Дамиров): метаданные первого
        # автора несут отчество ("Валерий Александрович Гуров" — Имя
        # Отчество Фамилия, 3 слова), второго — без ("Рафаэль Дамиров" —
        # 2 слова). Конвертер разворачивал в "Фамилия Имя" только РОВНО
        # двухсловные варианты — трёхсловный возвращался БЕЗ изменений,
        # давая испорченный порядок "Валерий Александрович Гуров, Дамиров
        # Рафаэль" (первый автор не развёрнут, второй — развёрнут).
        records = [
            _rec(r"Гуров Валерий\Завод\01.fb2", "Гуров Валерий",
                 "Валерий Александрович Гуров; Рафаэль Дамиров"),
            _rec(r"Гуров Валерий\Завод\02.fb2", "Гуров Валерий",
                 "Валерий Александрович Гуров; Рафаэль Дамиров"),
        ]

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert all(r.proposed_author == "Гуров Валерий, Дамиров Рафаэль" for r in records)

    def test_noisy_minority_metadata_does_not_widen_majority_clean_series(self):
        # Реальный случай (баг №58, отдельная реальная библиотека):
        # "Владимир Малый, Тёмные Окна - Сборник произведений" — 1 файл из
        # 6 несёт побочный, шумный <author> "Тёмные Окна" (имя вселенной,
        # ошибочно затянутое как соавтор), остальные 5 — честно только
        # "Владимир Малый". Наивное доверие метаданным ОДНОЙ записи
        # расширило бы автора и здесь — большинство (5 "чистых" против 1
        # "шумного") должно защитить от этого.
        records = [
            _rec(r"Владимир Малый\01.fb2", "Малый Владимир", "Владимир Малый"),
            _rec(r"Владимир Малый\02.fb2", "Малый Владимир", "Владимир Малый"),
            _rec(r"Владимир Малый\03.fb2", "Малый Владимир", "Владимир Малый"),
            _rec(r"Владимир Малый\04.fb2", "Малый Владимир", "Владимир Малый"),
            _rec(r"Владимир Малый\05.fb2", "Малый Владимир", "Владимир Малый"),
            _rec(r"Владимир Малый\06.fb2", "Малый Владимир", "Тёмные Окна; Владимир Малый"),
        ]
        for r in records:
            r.proposed_series = "Владимир Малый"

        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert all(r.proposed_author == "Малый Владимир" for r in records)
