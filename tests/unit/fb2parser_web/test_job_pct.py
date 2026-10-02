"""Общий расчёт процента для прогресс-баров фоновых задач (раньше был
скопирован в семь view)."""
import pytest

from fb2parser_web.views import _job_pct


@pytest.mark.parametrize("processed, total, expected", [
    (0, 0, 0), (5, 0, 0), (1, 3, 33), (3, 3, 100), (7, 3, 100),
])
def test_job_pct(processed, total, expected):
    assert _job_pct({"processed": processed, "total": total}) == expected


def test_missing_total_is_zero():
    assert _job_pct({"processed": 4}) == 0
