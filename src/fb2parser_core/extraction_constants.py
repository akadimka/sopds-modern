"""
Константы для извлечения авторов и серий из различных источников.

Определяет приоритеты источников и конфигурацию уровня уверенности.

ПРИОРИТЕТЫ ВСЕГДА ЧИТАЮТСЯ ИЗ config.json И НИКОГДА НЕ МЕНЯЮТСЯ!
"""

try:
    from settings_manager import SettingsManager
    _settings = SettingsManager('config.json')
    _priority_config = _settings.settings.get('extraction_priority_order', {})
    _author_priorities = _priority_config.get('author', {})
    _series_priorities = _priority_config.get('series', {})
except Exception:
    # Fallback если конфиг не загрузился
    _author_priorities = {'FOLDER_STRUCTURE': 3, 'FILENAME': 2, 'FB2_METADATA': 1}
    _series_priorities = {'FOLDER_STRUCTURE': 3, 'FILENAME': 2, 'FB2_METADATA': 1}


class AuthorExtractionPriority:
    """
    Приоритет источников для извлечения авторов (ИЗ CONFIG.JSON).
    
    Числовое значение определяет приоритет: выше число = выше приоритет.
    
    ПРИОРИТЕТЫ:
    - FOLDER_STRUCTURE = 3 (МАКСИМАЛЬНЫЙ)
    - FILENAME = 2 (СРЕДНИЙ)
    - FB2_METADATA = 1 (МИНИМАЛЬНЫЙ)
    """
    FOLDER_STRUCTURE = _author_priorities.get('FOLDER_STRUCTURE', 3)
    FILENAME = _author_priorities.get('FILENAME', 2)
    FB2_METADATA = _author_priorities.get('FB2_METADATA', 1)
    
    # Порядок итерации (от нижнего к верхнему приоритету)
    ORDER = sorted([
        ('folder', FOLDER_STRUCTURE),
        ('filename', FILENAME),
        ('metadata', FB2_METADATA)
    ], key=lambda x: x[1])
    
    # Человеко-читаемые названия
    NAMES = {
        FOLDER_STRUCTURE: 'folder',
        FILENAME: 'filename',
        FB2_METADATA: 'metadata'
    }
    
    @classmethod
    def get_name(cls, priority: int) -> str:
        """Получить текстовое название по приоритету."""
        return cls.NAMES.get(priority, 'unknown')
    
    @classmethod
    def is_valid(cls, priority: int) -> bool:
        """Проверить, корректный ли приоритет."""
        return priority in cls.NAMES


class SeriesExtractionPriority:
    """
    Приоритет источников для извлечения серий (ИЗ CONFIG.JSON).
    
    Числовое значение определяет приоритет: выше число = выше приоритет.
    
    ПРИОРИТЕТЫ:
    - FOLDER_STRUCTURE = 3 (МАКСИМАЛЬНЫЙ)
    - FILENAME = 2 (СРЕДНИЙ)
    - FB2_METADATA = 1 (МИНИМАЛЬНЫЙ)
    """
    FOLDER_STRUCTURE = _series_priorities.get('FOLDER_STRUCTURE', 3)
    FILENAME = _series_priorities.get('FILENAME', 2)
    FB2_METADATA = _series_priorities.get('FB2_METADATA', 1)
    
    # Порядок итерации (от нижнего к верхнему приоритету)
    ORDER = sorted([
        ('folder', FOLDER_STRUCTURE),
        ('filename', FILENAME),
        ('metadata', FB2_METADATA)
    ], key=lambda x: x[1])
    
    NAMES = {
        FOLDER_STRUCTURE: 'folder',
        FILENAME: 'filename',
        FB2_METADATA: 'metadata'
    }
    
    @classmethod
    def get_name(cls, priority: int) -> str:
        """Получить текстовое название по приоритету."""
        return cls.NAMES.get(priority, 'unknown')
    
    @classmethod
    def is_valid(cls, priority: int) -> bool:
        """Проверить, корректный ли приоритет."""
        return priority in cls.NAMES


class ConfidenceLevel:
    """Уровни уверенности для результатов извлечения."""
    
    # Структура папок обычно менее надежна
    FOLDER_MIN = 0.60
    FOLDER_MAX = 0.80
    
    # Имя файла - средняя надежность
    FILENAME_MIN = 0.60
    FILENAME_MAX = 0.80
    
    # Метаданные FB2 - самые надежные
    FB2_MIN = 0.70
    FB2_MAX = 0.95
    
    # Минимальная уверенность для принятия результата
    MIN_ACCEPTABLE = 0.50


# Имена папок, совпадающие с расширениями файлов, которые нужно прозрачно пропускать
# при анализе структуры пути.
# Структура "Автор\fb2\Серия\книга.fb2" обрабатывается как "Автор\Серия\книга.fb2".
FILE_EXTENSION_FOLDER_NAMES: frozenset = frozenset({
    'fb2', 'rtf', 'pdf', 'doc', 'docx', 'txt', 'epub',
    'djvu', 'djv', 'mobi', 'azw', 'azw3', 'lit', 'lrf',
    'html', 'htm', 'odt', 'zip', 'rar', '7z',
})
# Нормализованные (нижний регистр, е́→е) имена папок, означающих «без серии».
# Если папка с таким именем встречается в пути, proposed_series должно остаться пустым.
NO_SERIES_FOLDER_NAMES: frozenset = frozenset({
    # вне серий
    'вне серий', 'вне серии',
    # без серий
    'без серии', 'без серий',
    # несерийное
    'несерийное', 'несерийный',
    # внесерийное
    'внесерийное',
    # отдельные произведения
    'отдельные произведения', 'отдельное произведение',
    # standalone
    'standalone',
})


def is_no_series_folder(folder_name: str, extra_names: frozenset = None) -> bool:
    """Return True if the folder name means 'books without a series'.

    Comparison is case-insensitive and treats е́ (ё) as е.
    extra_names: optional frozenset of user-defined names loaded from config
                 (no_series_folder_names). Built-in NO_SERIES_FOLDER_NAMES
                 always acts as a fallback.
    """
    normalized = folder_name.lower().replace('е́', 'е').replace('ё', 'е')
    if extra_names and normalized in extra_names:
        return True
    return normalized in NO_SERIES_FOLDER_NAMES

class FilterReason:
    """Причины, по которым значение может быть отфильтровано."""
    
    IN_BLACKLIST = 'in_blacklist'           # Совпадает с файлом в черном списке
    EMPTY_VALUE = 'empty_value'             # Пустое значение
    INVALID_FORMAT = 'invalid_format'       # Некорректный формат
    LOW_CONFIDENCE = 'low_confidence'       # Низкая уверенность
    BLACKLIST_KEYWORDS = 'blacklist_keywords'  # Содержит слова из черного списка


class ExtractionResult:
    """Базовая структура результата извлечения."""
    
    def __init__(
        self,
        value: str,
        priority: int,
        raw_value: str = None,
        confidence: float = 0.7,
        pattern_used: str = None,
        pattern_index: int = None,
        extracted_groups: dict = None,
        is_filtered: bool = False,
        filter_reasons: list = None
    ):
        """
        Инициализация результата извлечения.
        
        Args:
            value: Извлеченное и нормализованное значение
            priority: Приоритет источника (из AuthorExtractionPriority или SeriesExtractionPriority)
            raw_value: Оригинальное значение до нормализации
            confidence: Уровень уверенности (0.0 - 1.0)
            pattern_used: Использованный паттерн (текст)
            pattern_index: Индекс паттерна в списке
            extracted_groups: Все группы, извлеченные из regex
            is_filtered: Было ли отфильтровано
            filter_reasons: Список причин фильтрации
        """
        self.value = value
        self.priority = priority
        self.raw_value = raw_value or value
        self.confidence = confidence
        self.pattern_used = pattern_used
        self.pattern_index = pattern_index
        self.extracted_groups = extracted_groups or {}
        self.is_filtered = is_filtered
        self.filter_reasons = filter_reasons or []
    
    def to_dict(self) -> dict:
        """Преобразовать в словарь."""
        return {
            'value': self.value,
            'priority': self.priority,
            'raw_value': self.raw_value,
            'confidence': self.confidence,
            'pattern_used': self.pattern_used,
            'pattern_index': self.pattern_index,
            'extracted_groups': self.extracted_groups,
            'is_filtered': self.is_filtered,
            'filter_reasons': self.filter_reasons
        }
    
    def __repr__(self) -> str:
        """Строковое представление."""
        status = "FILTERED" if self.is_filtered else "OK"
        priority_name = AuthorExtractionPriority.get_name(self.priority)
        return f"ExtractionResult({self.value}, priority={priority_name}, conf={self.confidence:.2f}, {status})"
