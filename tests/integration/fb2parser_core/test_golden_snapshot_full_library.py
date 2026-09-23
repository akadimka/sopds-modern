"""Golden-снапшот регрессионный тест на ПОЛНОЙ фикстур-библиотеке
(tests/data/regen_library, 319 файлов, собрана из реальных Test2-структур
— см. scripts/build_regen_fixtures.py).

Часть "размышления о хрупкости эвристического каскада" (docs/quality-
roadmap.md, баг №109), часть 3: остальные тесты в tests/*/fb2parser_core/
проверяют ОТДЕЛЬНЫЕ разобранные краевые случаи по отдельности — но не
защищают от побочного изменения поведения где-то ещё в тех же 319 файлах.
Этот тест перегоняет всю библиотеку заново и построчно сверяет с
эталонным снапшотом (tests/data/regen_library_golden_snapshot.csv): любое
расхождение, даже в файле, никак не относящемся к текущей правке,
всплывает сразу и с конкретным диффом по полям — без ручной охоты за
find_groups()/git stash, которая раньше занимала больше всего времени в
подобных расследованиях.

Если разработчик СОЗНАТЕЛЬНО меняет поведение и новые результаты
проверены и признаны верными — эталонный файл перестраивается через
`python scripts/build_golden_snapshot.py` и коммитится вместе с правкой.
Автоматической перестройки при запуске тестов НЕТ и не должно быть —
иначе тест никогда не сможет поймать регрессию.

Пайплайн запускается через scripts/build_golden_snapshot.py В ОТДЕЛЬНОМ
ПРОЦЕССЕ (subprocess), а не напрямую в процессе pytest. Причина — найдена
именно этим тестом: `AuthorName` (name_normalizer.py) держит несколько
class-level кэшей (известные имена, частицы имён и т.п.), которые
инициализируются от config-пути ПЕРВОГО вызова `set_config_path()` в
процессе; если КАКОЙ-ТО другой тестовый модуль в том же pytest-прогоне
успевает вызвать его раньше (напр. test_author_surname_ending_like_
patronymic.py в своём setup_module), последующий прогон конвейера в ТОМ
ЖЕ процессе может дать другой (не обязательно неверный, но ДРУГОЙ)
результат для отдельных записей — напр. "Осман Ричард Томас" против
"Осман Ричард" для одного и того же реального файла. Это не воспроизводится
при изолированном прогоне и не обязательно означает баг в самой логике
консенсуса — но делает тест на межпроцессном уровне порядко-зависимым,
что подрывает его смысл как регрессионного стража. Отдельный процесс —
тот же способ, каким строится сам эталонный снапшот, поэтому сравнение
всегда яблоки-с-яблоками.
"""
import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_golden_snapshot.py"
SNAPSHOT_PATH = REPO_ROOT / "tests" / "data" / "regen_library_golden_snapshot.csv"

# Тот же порядок полей, что и scripts/build_golden_snapshot.py — намеренно
# без content_hash (чистый хэш байтов файла, не решение regen_csv) и без
# decision_log (текст трассировки для человека, не для построчного diff).
FIELDS = (
    "file_path", "metadata_authors", "proposed_author", "author_source",
    "metadata_series", "proposed_series", "series_source", "file_title",
    "metadata_genre", "series_number", "series_number_source",
)


def _load_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, f"Пустой или отсутствующий CSV: {path}"
    return {row["file_path"]: row for row in rows}


def _load_golden():
    return _load_csv(SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def actual_by_path(tmp_path_factory):
    out_csv = tmp_path_factory.mktemp("golden_snapshot_run") / "actual.csv"
    env = dict(os.environ, DJANGO_SETTINGS_MODULE=os.environ.get(
        "DJANGO_SETTINGS_MODULE", "sopds.settings.local"))
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), str(out_csv)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, (
        f"scripts/build_golden_snapshot.py упал (код {result.returncode}):\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
    return _load_csv(out_csv)


def test_golden_snapshot_same_file_set(actual_by_path):
    golden = _load_golden()
    missing = sorted(set(golden) - set(actual_by_path))
    extra = sorted(set(actual_by_path) - set(golden))
    assert not missing and not extra, (
        f"Набор файлов изменился с момента golden-снапшота.\n"
        f"Пропали из результата: {missing}\n"
        f"Новые, которых нет в снапшоте: {extra}\n"
        f"Если это ожидаемо (напр. добавлена новая fixture-структура) — "
        f"перестройте снапшот: python scripts/build_golden_snapshot.py"
    )


def test_golden_snapshot_no_field_regressions(actual_by_path):
    golden = _load_golden()
    diffs = []
    for file_path, golden_row in sorted(golden.items()):
        actual_row = actual_by_path.get(file_path)
        if actual_row is None:
            continue  # уже отражено в test_golden_snapshot_same_file_set
        for field in FIELDS:
            if field == "file_path":
                continue
            golden_val = golden_row[field]
            actual_val = actual_row[field]
            if actual_val != golden_val:
                diffs.append(
                    f"  {file_path} :: {field}: golden={golden_val!r} actual={actual_val!r}"
                )
    assert not diffs, (
        "Поведение regen_csv изменилось по сравнению с golden-снапшотом "
        f"({len(diffs)} расхождений):\n" + "\n".join(diffs) + "\n\n"
        "Если новое поведение проверено и верно — перестройте снапшот: "
        "python scripts/build_golden_snapshot.py"
    )
