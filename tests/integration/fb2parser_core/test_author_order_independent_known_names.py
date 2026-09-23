"""Регрессия для `RegenCSVService.__init__` — docs/quality-roadmap.md,
"Размышление о хрупкости эвристического каскада", часть 3 (продолжение).

Реальный случай, найденный golden-снапшот тестом
(test_golden_snapshot_full_library.py): один и тот же файл ("Осман
Ричард - Клуб убийств по четвергам", метаданные "Ричард Томас Осман" —
наст. автор Richard Osman) давал РАЗНЫЙ proposed_author в зависимости от
того, вызывался ли где-то в процессе `AuthorName.set_config_path()` ДО
конструирования `RegenCSVService`, хотя аргумент вызова был тем же самым
путём к app_settings.json.

Причина: `AuthorName` (name_normalizer.py) держит class-level кэш
известных имён, заполняемый ПРИ ПЕРВОМ вызове `set_config_path()`. Этот
вызов раньше происходил только внутри `AuthorNormalizer.__init__()`,
конструируемого впервые лишь в PASS 3 (passes/pass3_normalize.py) — но
PASS 2's `prebuild_author_cache()` уже строит `AuthorName(...)` РАНЬШЕ.
На свежем процессе (без стороннего раннего вызова) кэш известных имён
там ещё пуст — разбор Фамилия/Имя для АВТОРОВ, обработанных на этом
шаге, шёл по менее точной ветке эвристики, что для этого конкретного
случая СЛУЧАЙНО давало похожий на верный результат; при заранее
заполненном кэше (как в проде после первого же PASS 3) разбор шёл по
другой ветке, терявшей среднее слово (см. соседний фикс в
name_normalizer.py, test_author_middle_word_not_dropped.py).

Фикс: `RegenCSVService.__init__()` теперь конструирует
`AuthorNormalizer(self.settings)` сразу, чтобы `AuthorName.
set_config_path()` был вызван с правильным путём ДО первого
использования `AuthorName` где бы то ни было в этом прогоне.

Этот тест воспроизводит ИМЕННО тот сценарий (лишний ранний вызов
set_config_path, как это делает test_author_surname_ending_like_
patronymic.py в своём setup_module) через subprocess — так же, как
test_golden_snapshot_full_library.py избегает false positive от порядка
запуска других тестовых файлов в общем pytest-прогоне.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPO_ROOT / "tests" / "data" / "regen_library"
TARGET_SUFFIX = "Осман Ричард - Клуб убийств по четвергам 1. Клуб убийств по четвергам.fb2"

_RUNNER = r"""
import sys, csv, tempfile
sys.path.insert(0, str(r"{src}"))

if {pollute}:
    from fb2parser_core.name_normalizer import AuthorName
    from fb2parser_core.settings_manager import SettingsManager
    from fb2parser_web.fb2parser_bridge import _config_path
    AuthorName.set_config_path(SettingsManager(_config_path()).app_settings_path)

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path as _cp2

with tempfile.TemporaryDirectory() as d:
    out_csv = str(__import__("pathlib").Path(d) / "regen.csv")
    service = RegenCSVService(_cp2())
    records = service.generate_csv(r"{library}", output_csv_path=out_csv)

for r in records:
    if r.file_path.endswith(r"{target}"):
        print("PROPOSED_AUTHOR=" + r.proposed_author)
"""


def _run(pollute: bool) -> str:
    script = _RUNNER.format(
        src=str(REPO_ROOT / "src"),
        pollute=pollute,
        library=str(LIBRARY_ROOT),
        target=TARGET_SUFFIX,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    for line in result.stdout.splitlines():
        if line.startswith("PROPOSED_AUTHOR="):
            return line[len("PROPOSED_AUTHOR="):]
    raise AssertionError(f"Target record not found. stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")


def test_author_result_independent_of_early_set_config_path_call():
    clean = _run(pollute=False)
    polluted = _run(pollute=True)
    assert clean == polluted == "Осман Ричард Томас", (
        f"clean={clean!r} polluted={polluted!r} — ожидался одинаковый, "
        f"полный результат в обоих сценариях"
    )
