# -*- coding: utf-8 -*-
"""Строит golden-снапшот `tests/data/regen_library_golden_snapshot.csv` —
эталонный результат полного прогона `RegenCSVService.generate_csv()` по
ВСЕЙ фикстур-библиотеке `tests/data/regen_library` (см.
scripts/build_regen_fixtures.py).

Часть "размышления о хрупкости эвристического каскада" (docs/quality-
roadmap.md, баг №109): любое, даже побочное изменение поведения
regen_csv/pass2-6 на ВСЕЙ библиотеке разом видно построчно в diff'е
golden-снапшота — без ручной охоты за find_groups()/git stash, которая
занимала больше всего времени в расследованиях этой сессии.

Разовый инструмент разработчика (не часть CI/тестового прогона) —
запускается СОЗНАТЕЛЬНО, когда новое поведение проверено и признано
правильным (а не автоматически при каждом запуске тестов — иначе тест
golden-снапшота никогда бы не смог поймать регрессию, он бы просто
"обновлял ожидание" под любое, в т.ч. ошибочное, поведение).

Использование (из корня проекта):
    python scripts/build_golden_snapshot.py [output_csv_path]

Без аргумента перезаписывает эталонный tests/data/regen_library_golden_
snapshot.csv. С аргументом — пишет в указанный путь вместо этого (так
тест ниже запускает этот же скрипт в ОТДЕЛЬНОМ процессе — см. комментарий
там про порядко-зависимость PASS2/6 через AuthorName-кэши, найденную
именно этим golden-снапшот тестом).

Тест, сверяющий текущее поведение с этим файлом:
    tests/integration/fb2parser_core/test_golden_snapshot_full_library.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fb2parser_core.regen_csv import RegenCSVService  # noqa: E402
from fb2parser_web.fb2parser_bridge import _config_path  # noqa: E402

LIBRARY_ROOT = Path(__file__).resolve().parent.parent / "tests" / "data" / "regen_library"
SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "data"
    / "regen_library_golden_snapshot.csv"
)

# Те же поля, что и BookRecord.to_tuple() (используется GUI-таблицей) —
# единый, уже установленный набор полей "то, что видит пользователь",
# без content_hash (шум: чистый хэш байтов файла, не решение regen_csv)
# и без decision_log (человекочитаемый текст трассировки, не предназначен
# для построчного diff — меняется от любой правки формулировок).
FIELDS = (
    "file_path", "metadata_authors", "proposed_author", "author_source",
    "metadata_series", "proposed_series", "series_source", "file_title",
    "metadata_genre", "series_number", "series_number_source",
)


def main():
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else SNAPSHOT_PATH

    service = RegenCSVService(_config_path())
    with tempfile.TemporaryDirectory() as tmp_dir:
        # output_csv_path=None пропускает _save_csv() целиком — а вместе с
        # ним и финальные пост-чеки, выполняющиеся ТОЛЬКО в момент
        # сохранения CSV (напр. _clear_collection_folder_series(), баг
        # №56) — см. соответствующий комментарий в
        # tests/integration/fb2parser_core/test_regen_edge_cases.py.
        # Снапшот должен отражать РЕАЛЬНОЕ поведение regen.csv, поэтому
        # указываем настоящий путь, как и остальные regression-тесты.
        tmp_csv = Path(tmp_dir) / "regen.csv"
        records = service.generate_csv(str(LIBRARY_ROOT), output_csv_path=str(tmp_csv))
    records = sorted(records, key=lambda r: r.file_path)

    with open(dest, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        for r in records:
            writer.writerow(getattr(r, field) for field in FIELDS)

    print(f"Снапшот записан: {dest} ({len(records)} записей)")


if __name__ == "__main__":
    main()
