"""
Утилиты для работы с FB2 и FB2.ZIP файлами.

fb2.zip — это обычный ZIP-архив, содержащий один .fb2 файл внутри.
Все функции прозрачно работают с обоими форматами.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import List

_CONTROL_CHARS_RE = re.compile(r'\s*[\x00-\x1f\x7f]+\s*')


def replace_control_chars(value: str) -> str:
    """Управляющие символы (перевод строки, табуляция…) в имени файла/папки —
    на пробел (баг №162): из названия книги «Чудовище маяка\\nи другие
    хонкаку» синхронизация делала имя файла с переводом строки. Пробелы
    схлопываются только вокруг заменённого — двойные пробелы, бывшие в
    имени изначально, не трогаются."""
    return _CONTROL_CHARS_RE.sub(' ', value or '')


def fb2_rglob(directory: Path) -> List[Path]:
    """Рекурсивно найти все FB2 и FB2.ZIP файлы в каталоге."""
    plain = list(directory.rglob('*.fb2'))
    zipped = list(directory.rglob('*.fb2.zip'))
    return sorted(plain + zipped, key=lambda p: str(p).lower())


MAX_FB2_UNCOMPRESSED_SIZE = 200 * 1024 * 1024  # 200 МБ — см. read_fb2_bytes (баг №97)

# Баг №136: <description> обычно в начале файла, и метаданные читаются из
# первых 64 КБ. Но бывают файлы, где перед ним стоит <binary> с обложкой
# («Нанана-Нана - Покорять подземелье…\01. Том 1.fb2»: <title-info> на
# 181-м КБ) — тогда жанр, автор и название не находились, и смена жанра
# «не срабатывала». Если в прочитанном начале нет конца <title-info>,
# дочитываем, но не дальше этого предела.
TITLE_INFO_SCAN_LIMIT = 32 * 1024 * 1024
TITLE_INFO_END_RE = re.compile(r'</(?:[\w.-]+:)?title-info\s*>')
_TITLE_INFO_END_BYTES_RE = re.compile(rb'</(?:[\w.-]+:)?title-info\s*>')


def read_fb2_head(path: Path, head_bytes: int = 65536) -> bytes:
    """Начало FB2 (байты XML, в т.ч. из .fb2.zip), гарантированно включающее
    конец <title-info>, если он есть в пределах TITLE_INFO_SCAN_LIMIT."""
    if path.name.lower().endswith('.zip'):
        raw = read_fb2_bytes(path)
        end = _TITLE_INFO_END_BYTES_RE.search(raw, 0, TITLE_INFO_SCAN_LIMIT)
        return raw[:max(head_bytes, end.end() if end else head_bytes)]
    with open(path, 'rb') as f:
        data = f.read(head_bytes)
        searched_from = 0
        while not _TITLE_INFO_END_BYTES_RE.search(data, searched_from) \
                and len(data) < TITLE_INFO_SCAN_LIMIT:
            chunk = f.read(4 * head_bytes)
            if not chunk:
                break
            # тег может разрезаться границей куска — перекрытие в 64 байта
            searched_from = max(0, len(data) - 64)
            data += chunk
    return data


def read_fb2_bytes(path: Path) -> bytes:
    """Прочитать содержимое FB2 (XML) из файла или zip-архива."""
    raw = path.read_bytes()
    if raw[:2] == b'PK':
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = zf.namelist()
                fb2_name = next(
                    (n for n in names if n.lower().endswith('.fb2')),
                    names[0] if names else None,
                )
                if fb2_name:
                    # Баг №97: без этой проверки крошечный по размеру
                    # .fb2.zip с объявленным огромным распакованным
                    # размером (zip-bomb) полностью разворачивался бы в
                    # память через zf.read() — этот путь дёргается на
                    # каждый файл при хешировании (metadata_cache.py),
                    # синхронизации и компиляции, не только при разборе
                    # одного конкретного файла.
                    if zf.getinfo(fb2_name).file_size > MAX_FB2_UNCOMPRESSED_SIZE:
                        raise ValueError(
                            f"'{fb2_name}' in {path.name} declares "
                            f"{zf.getinfo(fb2_name).file_size} bytes uncompressed "
                            f"(> {MAX_FB2_UNCOMPRESSED_SIZE}) - refusing, looks like a zip bomb"
                        )
                    return zf.read(fb2_name)
        except Exception:
            pass
    return raw


def write_fb2_bytes(path: Path, xml_bytes: bytes) -> None:
    """Записать XML обратно в файл с сохранением формата (zip или plain).

    Если файл был zip — пересохраняет zip с тем же именем внутри архива.
    """
    if path.name.lower().endswith('.fb2.zip'):
        inner_name = path.name[:-4]  # strip .zip → "book.fb2"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(inner_name, xml_bytes)
        path.write_bytes(buf.getvalue())
    else:
        # Для обычного .fb2 — просто запись
        path.write_bytes(xml_bytes)


def compress_fb2_file(path: Path) -> int:
    """Сжать плоский .fb2 файл в .fb2.zip рядом, удалить оригинал.

    Пишет во временный файл, проверяет его round-trip (распакованное
    содержимое совпадает с оригиналом побайтово) и только потом заменяет
    оригинал — при сбое посреди операции реальный файл не теряется.

    Returns:
        Число освобождённых байт (может быть отрицательным для очень
        маленьких файлов, где zip-заголовок съедает всю экономию).

    Raises:
        ValueError: если path уже .fb2.zip.
        Exception: при ошибке чтения/записи/round-trip — оригинал не тронут.
    """
    if path.name.lower().endswith('.fb2.zip'):
        raise ValueError(f"Уже сжат: {path}")

    target = path.with_name(path.name + '.zip')
    tmp = path.with_name(path.name + '.zip.tmp')
    # Рядом уже лежит «книга.fb2.zip» — rename молча перезаписал бы его
    # (на POSIX), а там может быть другая редакция/текст.
    if target.exists():
        raise FileExistsError(f"Уже есть {target.name} — не перезаписываю")
    original_bytes = path.read_bytes()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(path.name, original_bytes)
    zip_bytes = buf.getvalue()

    tmp.write_bytes(zip_bytes)
    try:
        with zipfile.ZipFile(tmp) as zf:
            if zf.read(path.name) != original_bytes:
                raise ValueError("Round-trip проверка не прошла: содержимое не совпадает")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    original_size = path.stat().st_size
    # Сначала архив под своим именем, потом удаление оригинала: при сбое между
    # шагами остаются оба файла, а не один .tmp.
    tmp.rename(target)
    path.unlink()
    return original_size - len(zip_bytes)


def has_fb2_files(directory: Path) -> bool:
    """True если в директории есть хотя бы один FB2/FB2.ZIP файл."""
    for _ in directory.rglob('*.fb2'):
        return True
    for _ in directory.rglob('*.fb2.zip'):
        return True
    return False
