"""Фикстуры для временного переопределения SOPDS_* конфигурации в тестах.

Раньше настройки жили в django-constance (БД-хранимые, с готовой тестовой
утилитой `constance.test.override_config`). После миграции на собственный
JSON-файловый прокси `SopdsConfig` (`opds_catalog.sopds_config.sopds_cfg`,
см. его докстринг) этот механизм исчез — `SopdsConfig.__setattr__` пишет
изменения СРАЗУ в реальный config.json на диске, что для тестов не годится
(мутирует настоящий файл, конфликтует при параллельных прогонах, не
восстанавливается при падении теста).

`override_config` подменяет атрибуты ПРЯМО в `__dict__` общего singleton'а
`sopds_cfg` через `object.__setattr__`/`object.__delattr__`, в обход
переопределённого `__setattr__` — обычный атрибут перекрывает `__getattr__`
(который иначе читает файл), поэтому диск не трогается вовсе. Все модули,
импортирующие `from opds_catalog.sopds_config import sopds_cfg as config`,
получают ссылку на ОДИН И ТОТ ЖЕ объект — переопределение видно им всем.
"""
from contextlib import contextmanager

import pytest

from opds_catalog.sopds_config import sopds_cfg

_MISSING = object()


@pytest.fixture
def override_config():
    """Возвращает context-manager-фабрику для временного переопределения
    SOPDS_* атрибутов: ``with override_config(SOPDS_AUTH=True): ...``
    """

    @contextmanager
    def _override(**kwargs):
        previous = {k: sopds_cfg.__dict__.get(k, _MISSING) for k in kwargs}
        try:
            for k, v in kwargs.items():
                object.__setattr__(sopds_cfg, k, v)
            yield
        finally:
            for k, prev in previous.items():
                if prev is _MISSING:
                    sopds_cfg.__dict__.pop(k, None)
                else:
                    object.__setattr__(sopds_cfg, k, prev)

    return _override


@pytest.fixture(autouse=True)
def _apply_override_config_marker(request, override_config):
    """Обрабатывает ``@pytest.mark.override_config(SOPDS_X=...)`` на тестах —
    эквивалент старого `constance`-маркера, но через `override_config` выше.
    """
    marker = request.node.get_closest_marker("override_config")
    if marker is None:
        yield
        return
    with override_config(**marker.kwargs):
        yield
