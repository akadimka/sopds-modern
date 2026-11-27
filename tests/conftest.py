"""Общие настройки для всего проекта тестирования.

Корневой conftest: содержит фикстуры, не требующие Django (BytesIO, ZIP-тесты),
а также регистрирует ``tests.fixtures`` для всех поддиректорий.
"""

import io
import zipfile

import pytest

pytest_plugins = ["tests.fixtures"]


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Кэш (счётчики видимости и т.п. — opds_catalog/visibility.py) не
    переходит из теста в тест: база откатывается, и новый пользователь
    получает id прежнего вместе с его закэшированными числами."""
    from django.core.cache import cache
    cache.clear()
    yield


@pytest.fixture(scope="session")
def fb2_with_cover():
    """FB2 файл с обложкой.

    Создаёт объект ``BytesIO`` с корректным FB2-файлом, содержащим обложку (binary-изображение).
    Используется для тестов, проверяющих извлечение обложек.

    :scope: session
    :returns: BytesIO с FB2-контентом
    :rtype: io.BytesIO
    """
    fb2_content = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:xlink="http://www.w3.org/1999/xlink">
<description>
    <title-info>
        <book-title>Test Book</book-title>
        <author>
            <first-name>Test</first-name>
            <last-name>Author</last-name>
        </author>
        <coverpage>
          <image xlink:href="#cover.jpg"/>
        </coverpage>
    </title-info>
</description>
<body>
</body>
<binary id="cover.jpg" content-type="image/jpeg">/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwC1AAD/2Q==</binary>
</FictionBook>"""
    return io.BytesIO(fb2_content.encode("utf-8"))


@pytest.fixture(scope="session")
def zipped_fb2_with_cover(fb2_with_cover):
    """ZIP архив, содержащий FB2 файл с обложкой.

    ZIP-архив, содержащий один файл ``book.fb2`` (с обложкой). Полезен при тестировании
    распаковки архивов.

    :scope: session
    :returns: BytesIO с ZIP-архивом
    :rtype: io.BytesIO
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        zip_file.writestr("book.fb2", fb2_with_cover.getvalue())
    zip_buffer.seek(0)
    return zip_buffer
