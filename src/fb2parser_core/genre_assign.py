"""
Сервис для присвоения жанра всем FB2 файлам в папке.

Изменяет значение тега <genre> в FB2 файлах:
- Удаляет все существующие теги <genre>
- Добавляет один новый тег <genre> с выбранным жанром
- Сохраняет изменения в файл
"""

import threading
import re
import html
import ctypes
import os
from pathlib import Path
from typing import Optional, Callable, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed


def _detect_optimal_workers(path: Path) -> int:
    """Определить оптимальное число потоков по типу диска."""
    try:
        anchor = str(path.resolve())
        # UNC-путь \\server\share — точно сеть
        if anchor.startswith('\\\\'):
            return 12
        drive = path.resolve().anchor  # например "C:\\" или "Z:\\"
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
        # 4 = DRIVE_REMOTE (mapped network drive)
        if drive_type == 4:
            return 12
        # 3 = DRIVE_FIXED (SSD/HDD), 2 = DRIVE_REMOVABLE
        # Для HDD конкурентный доступ вреден — используем 1.
        # Отличить SSD от HDD без WMI сложно, поэтому консервативно: 4.
        # На SSD это нейтрально, на HDD — приемлемо.
        return 4
    except Exception:
        return 4

try:
    from logger import Logger
except ImportError:
    from .logger import Logger

try:
    from fb2_utils import MAX_FB2_UNCOMPRESSED_SIZE
except ImportError:
    from .fb2_utils import MAX_FB2_UNCOMPRESSED_SIZE


class GenreAssignmentService:
    """Сервис для присвоения жанра FB2 файлам."""
    
    # Namespaces для FB2
    FB2_NAMESPACE = 'http://www.gribuser.ru/xml/fictionbook/2.0'
    
    def __init__(self, logger=None):
        """Инициализация сервиса."""
        self.logger = logger if logger is not None else Logger()
        self.processed_count = 0
    
    def assign_genre_to_folder(
        self,
        folder_path: str,
        genre_name: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        completion_callback: Optional[Callable[[int], None]] = None
    ) -> int:
        """
        Присвоить жанр всем FB2 файлам в папке (рекурсивно).
        
        Args:
            folder_path: Путь к папке с FB2 файлами
            genre_name: Название жанра для присвоения
            progress_callback: Функция для обновления прогресса
                Вызывается как: progress_callback(current, total, filename)
            completion_callback: Функция для завершения
                Вызывается как: completion_callback(count)
        
        Returns:
            Количество обработанных файлов
        """
        # Validate input
        if not folder_path or not str(folder_path).strip():
            self.logger.log("ОШИБКА: folder_path не задан или пуст!")
            return 0
        
        if not genre_name or not str(genre_name).strip():
            self.logger.log("ОШИБКА: genre_name не задан или пуст!")
            return 0
        
        # pathlib.Path already handles '/' on both Windows and POSIX; forcing
        # backslashes here broke every lookup on Linux deployments (folder.exists()
        # always False, since '\' is just a regular filename character on POSIX).
        folder_path_normalized = str(folder_path).strip()

        folder = Path(folder_path_normalized)
        
        if not folder.exists():
            self.logger.log(f"Папка не найдена: {folder_path_normalized}")
            return 0
        
        self.logger.log(f"Папка сканирования: {folder_path_normalized}")
        self.logger.log(f"Поиск файлов (рекурсивно)...")
        
        # Найти все FB2 файлы (*.fb2 покрывает оба случая на Windows)
        fb2_files = list(folder.rglob('*.fb2')) + list(folder.rglob('*.FBZ'))
        
        self.logger.log(f"Найдено файлов: {len(fb2_files)}")
        
        if fb2_files:
            for fb2_file in fb2_files[:5]:  # Показать первые 5
                self.logger.log(f"  - {fb2_file.relative_to(folder)}")
            if len(fb2_files) > 5:
                self.logger.log(f"  ... и ещё {len(fb2_files) - 5} файлов")
        
        if not fb2_files:
            self.logger.log(f"FB2 файлы не найдены в {folder_path}")
            return 0
        
        max_workers = _detect_optimal_workers(folder)
        self.logger.log(
            f"Начато присвоение жанра '{genre_name}' для {len(fb2_files)} файлов "
            f"(потоков: {max_workers})"
        )

        self.processed_count = 0
        total = len(fb2_files)
        completed = [0]  # изменяемый счётчик для замыкания
        lock = threading.Lock()

        def _process(fb2_path: Path):
            ok = self._assign_genre_to_file(fb2_path, genre_name)
            with lock:
                completed[0] += 1
                idx = completed[0]
                if progress_callback:
                    progress_callback(idx, total, fb2_path.name)
                if ok:
                    self.processed_count += 1
                    self.logger.log(f"  [{idx}/{total}] Жанр присвоен: {fb2_path.name}")
                else:
                    self.logger.log(f"  [{idx}/{total}] ОШИБКА: {fb2_path.name}")
            return ok

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_process, p) for p in fb2_files]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    self.logger.log(f"  ОШИБКА потока: {e}")

        self.logger.log(f"Завершено! Жанр изменен у {self.processed_count} файлов")
        
        if completion_callback:
            completion_callback(self.processed_count)

        return self.processed_count

    def assign_genre_to_files(
        self,
        file_paths: List[str],
        genre_name: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, bool]:
        """Присвоить жанр явному списку файлов (не рекурсивный обход папки).

        В отличие от ``assign_genre_to_folder`` — для точечного присвоения
        разным файлам ОДНОЙ папки (напр. разножанрового сборника) разных
        целевых жанров: каждый вызов затрагивает только переданные файлы.

        Args:
            file_paths: Абсолютные пути к FB2/FBZ файлам.
            genre_name: Название жанра для присвоения.
            progress_callback: Вызывается как progress_callback(current, total, filename).

        Returns:
            {абсолютный_путь: успех} — по одной записи на каждый переданный файл.
        """
        if not genre_name or not str(genre_name).strip():
            self.logger.log("ОШИБКА: genre_name не задан или пуст!")
            return {str(p): False for p in file_paths}

        paths = [Path(p) for p in file_paths]
        total = len(paths)
        results: Dict[str, bool] = {}
        completed = [0]
        lock = threading.Lock()

        def _process(fb2_path: Path):
            ok = self._assign_genre_to_file(fb2_path, genre_name)
            with lock:
                completed[0] += 1
                idx = completed[0]
                if progress_callback:
                    progress_callback(idx, total, fb2_path.name)
            return ok

        max_workers = min(8, max(1, total))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_path = {pool.submit(_process, p): p for p in paths}
            for fut in as_completed(future_to_path):
                p = future_to_path[fut]
                try:
                    results[str(p)] = fut.result()
                except Exception as e:
                    self.logger.log(f"  ОШИБКА потока: {e}")
                    results[str(p)] = False

        return results

    def _assign_genre_to_file(self, fb2_path: Path, genre_name: str) -> bool:
        """
        Присвоить жанр одному FB2 файлу.
        
        Логика:
        1. Прочитать XML
        2. Найти все теги <genre> в разделе <description>/<title-info>
        3. Удалить все найденные теги <genre>
        4. Добавить один новый тег <genre> с выбранным жанром
        5. Сохранить файл
        
        Args:
            fb2_path: Путь к FB2 файлу
            genre_name: Название жанра
        
        Returns:
            True если успешно, False при ошибке
        """
        try:
            # Сначала проверить, не ZIP ли это (FBZ или архивированный FB2)
            content = None
            content_encoding = 'utf-8'  # default; overridden below
            
            try:
                import zipfile
                if zipfile.is_zipfile(fb2_path):
                    # Это ZIP архив
                    with zipfile.ZipFile(fb2_path, 'r') as zf:
                        # Найти XML файл внутри архива
                        xml_files = [f for f in zf.namelist() if f.endswith('.xml') or f.endswith('.fb2')]
                        if not xml_files:
                            self.logger.log(f"ОШИБКА: {fb2_path} - в архиве не найдены XML файлы")
                            return False
                        
                        # Баг №97: без проверки размера крошечный по
                        # размеру .fb2.zip с огромным заявленным
                        # распакованным размером (zip-bomb) полностью
                        # разворачивался бы в память через f.read().
                        info = zf.getinfo(xml_files[0])
                        if info.file_size > MAX_FB2_UNCOMPRESSED_SIZE:
                            self.logger.log(
                                f"ОШИБКА: {fb2_path} - '{xml_files[0]}' в архиве "
                                f"объявляет {info.file_size} байт распакованным "
                                f"(> {MAX_FB2_UNCOMPRESSED_SIZE}) - похоже на zip-bomb, отказ"
                            )
                            return False

                        # Прочитать первый XML файл
                        with zf.open(xml_files[0]) as f:
                            content = f.read().decode('utf-8-sig', errors='replace')
                            content_encoding = 'utf-8'
            except (zipfile.BadZipFile, ImportError):
                pass
            
            # Если не ZIP, читаем как обычный текстовый файл
            if content is None:
                raw_bytes = fb2_path.read_bytes()

                # Определение кодировки из XML declaration, если указана
                declared_encoding = None
                decl_match = re.search(br'<\?xml[^>]*encoding\s*=\s*["\']([^"\']+)["\']', raw_bytes, re.IGNORECASE)
                if decl_match:
                    try:
                        declared_encoding = decl_match.group(1).decode('ascii', errors='ignore')
                    except Exception:
                        declared_encoding = None

                # Список испытания кодировок (первой ставим объявленную, если есть)
                enc_candidates = []
                if declared_encoding:
                    enc_candidates.append(declared_encoding)

                enc_candidates.extend(['utf-8-sig', 'utf-8', 'cp1251', 'latin-1'])

                seen_encodings = set()
                good_content = None

                # Первая попытка - строгий декод, не потеряв символы
                for encoding in enc_candidates:
                    if not encoding or encoding.lower() in seen_encodings:
                        continue
                    seen_encodings.add(encoding.lower())
                    try:
                        candidate = raw_bytes.decode(encoding, errors='strict')
                    except (LookupError, UnicodeDecodeError):
                        continue
                    if candidate.strip().startswith('<?xml') or candidate.strip().startswith('<'):
                        good_content = candidate
                        content_encoding = encoding
                        break

                # Вторая попытка - более мягкая, если строгая не сработала
                if good_content is None:
                    for encoding in enc_candidates:
                        if not encoding or encoding.lower() in seen_encodings:
                            continue
                        seen_encodings.add(encoding.lower())
                        try:
                            candidate = raw_bytes.decode(encoding, errors='replace')
                        except (LookupError, UnicodeDecodeError):
                            continue
                        if candidate.strip().startswith('<?xml') or candidate.strip().startswith('<'):
                            good_content = candidate
                            content_encoding = encoding
                            break

                content = good_content

                if content is None:
                    self.logger.log(f"ОШИБКА: {fb2_path} - не удалось прочитать с известными кодировками")
                    return False

                # Если мы прочитали с заменой символов, попробуем не терять данные: дать предпочтение cp1251 для файла с явно cp1251 xml
                if declared_encoding and declared_encoding.lower() in ['cp1251', 'windows-1251'] and content_encoding not in ['cp1251', 'windows-1251']:
                    try:
                        content_cp = raw_bytes.decode('cp1251', errors='strict')
                        if content_cp.strip().startswith('<?xml') or content_cp.strip().startswith('<'):
                            content = content_cp
                            content_encoding = 'cp1251'
                        
                    except UnicodeDecodeError:
                        pass
            
            # Проверить, что это валидный XML
            content_stripped = content.strip()
            if not content_stripped.startswith('<?xml') and not content_stripped.startswith('<'):
                self.logger.log(f"ОШИБКА: {fb2_path} - не валидный XML файл")
                return False
            
            # Используем чистый regex подход вместо ElementTree, чтобы избежать проблем
            # с undefined namespace prefixes - это более надежно для malformed FB2 файлов
            has_bom = content.startswith('\ufeff')
            
            # Проверить наличие title-info раздела
            if not re.search(r'<(?:fb:)?title-info', content):
                self.logger.log(f"ОШИБКА: {fb2_path} - не найден раздел <title-info>")
                return False
            
            # Найти все существующие genre теги с их значениями в title-info и удалить их
            # Regex ищет <genre ...> ... </genre> с любыми атрибутами и значениями
            # Это работает независимо от namespace префиксов

            genre_pattern = r'<(?:fb:)?genre[^>]*>.*?</(?:fb:)?genre>'
            result_text = re.sub(genre_pattern, '', content, flags=re.DOTALL | re.IGNORECASE)
            
            # Найти позицию для вставки нового genre тега
            # Ищем </title-info> и вставляем перед ней
            title_info_close = re.search(r'</(?:fb:)?title-info>', result_text)
            
            if title_info_close:
                # Вставляем новый genre тег перед </title-info>
                insert_pos = title_info_close.start()
                safe_genre = html.escape(genre_name)
                new_genre_tag = f'<genre>{safe_genre}</genre>\n  '
                result_text = result_text[:insert_pos] + new_genre_tag + result_text[insert_pos:]
            else:
                self.logger.log(f"ОШИБКА: не найден </title-info> в {fb2_path}")
                return False
            
            # Сохранить файл с ОРИГИНАЛЬНОЙ кодировкой (не меняем ео на UTF-8)
            # Обновляем XML-декларацию если кодировка изменилась
            if content_encoding.lower().replace('-', '').replace('_', '') in ('utf8', 'utf8sig'):
                # Уже UTF-8: просто записываем с BOM если был
                encoding_to_write = 'utf-8-sig' if has_bom else 'utf-8'
            else:
                # Не-UTF-8 (например cp1251): обновить XML-декларацию и записать обратно
                result_text = re.sub(
                    r'(<\?xml[^>]*encoding\s*=\s*["\'])[^"\']+(["\'])',
                    lambda m: m.group(1) + content_encoding + m.group(2),
                    result_text, count=1
                )
                encoding_to_write = content_encoding

            # Пишем во временный файл рядом и атомарно подменяем оригинал —
            # если процесс прервётся посреди записи (например, ассайн жанра
            # на большой папке не уложился в таймаут воркера gunicorn),
            # оригинальный файл останется целым вместо усечённого/битого.
            tmp_path = fb2_path.with_name(fb2_path.name + '.tmp')
            try:
                with open(tmp_path, 'w', encoding=encoding_to_write, errors='replace') as f:
                    f.write(result_text)
                os.replace(tmp_path, fb2_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

            return True
        
        except Exception as e:
            self.logger.log(f"Ошибка при обработке {fb2_path}: {str(e)}")
            return False


if __name__ == '__main__':
    service = GenreAssignmentService()
    print("GenreAssignmentService инициализирован")
