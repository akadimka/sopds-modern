"""
Pattern Converter Module / Модуль преобразования шаблонов

Преобразует простые пользовательские шаблоны в регулярные выражения.

/ Converts user-friendly patterns to regex patterns.
"""
import re


def _normalize_group_name(name: str) -> str:
    """
    Нормализовать имя группы для regex - убрать недопустимые символы.
    
    Regex группа не может содержать пробелы, точки и другие спецсимволы.
    Заменяем их на подчёркивание.
    
    Примеры:
    - "series. service_words" → "series_service_words"
    - "Author" → "author"
    - "Title - Name" → "title_name"
    """
    if not name:
        return "group"
    
    # Преобразуем в нижний регистр
    normalized = name.lower()
    
    # Заменяем пробелы, точки, дефисы и другие символы на подчёркивание
    normalized = re.sub(r'[^a-z0-9_]', '_', normalized)
    
    # Убираем ведущие/trailing подчёркивания
    normalized = normalized.strip('_')
    
    # Если имя пусто или начинается с цифры - добавляем префикс
    if not normalized or normalized[0].isdigit():
        normalized = 'g_' + normalized
    
    return normalized


def convert_simple_pattern_to_regex(pattern_str: str) -> str:
    """
    Преобразует простой шаблон в регулярное выражение.
    
    Примеры:
    - "(Author) - Title" → "^\\((?P<author>[^)]+)\\)\\s*-\\s+(?P<title>.+)$"
    - "[Series] - (Author)" → "^\\[(?P<series>[^\\]]+)\\]\\s*-\\s+\\((?P<author>[^)]+)\\)$"
    - "Author - Title" → "^(?P<author>.+?)\\s*-\\s+(?P<title>.+)$"
    - "Author, Author. Title" → "^(?P<author>.+?),\s*(?P<author_2>.+?)\.\s+(?P<title>.+)$"
    
    Правила:
    - (Name) - содержимое в круглых скобках, группа = name.lower()
    - [Name] - содержимое в квадратных скобках, группа = name.lower()
    - Name - текст без скобок, группа = name.lower()
    - Если группа повторяется, добавляется суффикс _2, _3 и т.д.
    """
    if not pattern_str or not isinstance(pattern_str, str):
        return ""
    
    pattern_str = pattern_str.strip()
    
    # Регулярное выражение для поиска всех групп (с/без скобок)
    # Ищет: (Name), [Name], "Name", «Name» или просто Name (между разделителями)
    token_pattern = r'\(([^)]+)\)|\[([^\]]+)\]|"([^"]+)"|«([^»]+)»|(\w+)'
    
    # Найти все токены и их позиции
    tokens = []
    last_end = 0
    group_name_counts = {}  # Отслеживать количество использований каждого имени
    
    for match in re.finditer(token_pattern, pattern_str):
        # Текст перед этим токеном
        before_text = pattern_str[last_end:match.start()]
        
        # Тип и содержимое токена
        bracket_group = match.group(1)  # (Name)
        square_group = match.group(2)   # [Name]
        quote_group = match.group(3)    # "Name"
        guillemet_group = match.group(4)  # «Name»
        plain_group = match.group(5)    # Name
        
        if bracket_group:
            base_group_name = _normalize_group_name(bracket_group)
            bracket_type = '()'
        elif square_group:
            base_group_name = _normalize_group_name(square_group)
            bracket_type = '[]'
        elif quote_group:
            base_group_name = _normalize_group_name(quote_group)
            bracket_type = 'quotes'
        elif guillemet_group:
            base_group_name = _normalize_group_name(guillemet_group)
            bracket_type = 'guillemets'
        elif plain_group:
            base_group_name = _normalize_group_name(plain_group)
            bracket_type = 'plain'
        
        # Обработка дублирующихся имен групп - добавляем суффикс _2, _3 и т.д.
        if base_group_name in group_name_counts:
            group_name_counts[base_group_name] += 1
            # Добавляем суффикс к имени группы
            if group_name_counts[base_group_name] == 2:
                # Для второго появления добавляем _2
                final_group_name = base_group_name + '_2'
            else:
                # Для третьего + добавляем соответствующий номер
                final_group_name = base_group_name + '_' + str(group_name_counts[base_group_name])
        else:
            # Первое появление - используем base имя без суффикса
            group_name_counts[base_group_name] = 1
            final_group_name = base_group_name
        
        tokens.append({
            'before': before_text,
            'name': final_group_name,
            'bracket_type': bracket_type
        })
        
        last_end = match.end()
    
    # Остаток строки после последнего токена
    remaining = pattern_str[last_end:]
    
    if not tokens:
        # Если токенов нет, экранируем строку как есть
        escaped = re.escape(pattern_str)
        return f"^{escaped}$"
    
    # Построить regex из токенов
    regex_parts = ['^']
    
    for i, token in enumerate(tokens):
        # Добавляем текст перед токеном (экранированный с гибким пробелом)
        before = token['before']
        if before:
            before_escaped = re.escape(before)
            # " - " (пробел-дефис-пробел) требует хотя бы один пробел с каждой стороны,
            # чтобы НЕ совпадать с дефисом в составных словах типа "Марк-Уве".
            before_escaped = before_escaped.replace(r'\ \-\ ', r'\s+-\s+')
            # Пробелы в разделителях — обязательные (хотя бы один).
            # "\s*" (ноль пробелов) позволяло "." в "2.0" быть разделителем,
            # превращая "Цивилизация 2.0 1" в series="Цивилизация 2", title="0 1. ...".
            before_escaped = before_escaped.replace(r'\ ', r'\s+')
            regex_parts.append(before_escaped)
        
        # Добавляем саму группу
        group_name = token['name']
        bracket_type = token['bracket_type']
        
        if bracket_type == '()':
            # (Name) - match content in ()
            regex_parts.append(r'\((?P<' + group_name + r'>[^)]+)\)')
        elif bracket_type == '[]':
            # [Name] - match content in []
            regex_parts.append(r'\[(?P<' + group_name + r'>[^\]]+)\]')
        elif bracket_type == 'quotes':
            # "Name" - match content in double quotes
            regex_parts.append(r'"(?P<' + group_name + r'>[^"]+)"')
        elif bracket_type == 'guillemets':
            # «Name» - match content in Russian guillemets
            regex_parts.append(r'«(?P<' + group_name + r'>[^»]+)»')
        else:  # plain
            # Name - match any content
            regex_parts.append(r'(?P<' + group_name + r'>.+?)')
    
    # Добавляем остаток (если он есть)
    if remaining:
        remaining_escaped = re.escape(remaining)
        remaining_escaped = remaining_escaped.replace(r'\ ', r'\s*')
        regex_parts.append(remaining_escaped)
    
    # Добавляем конец строки
    regex_parts.append('$')
    
    result = ''.join(regex_parts)
    return result


def extract_group_names(pattern_str: str) -> list:
    """
    Извлекает названия групп из простого шаблона.
    
    Примеры:
    - "(Author) - Title" → ['author', 'title']
    - "[Series] (Author)" → ['series', 'author']
    """
    token_pattern = r'\(([^)]+)\)|\[([^\]]+)\]|(\w+)'
    group_names = []
    
    for match in re.finditer(token_pattern, pattern_str):
        bracket_group = match.group(1)
        square_group = match.group(2)
        plain_group = match.group(3)
        
        if bracket_group:
            group_names.append(_normalize_group_name(bracket_group))
        elif square_group:
            group_names.append(_normalize_group_name(square_group))
        elif plain_group:
            group_names.append(_normalize_group_name(plain_group))
    
    return group_names


def compile_patterns(pattern_strings: list) -> list:
    """
    Преобразует список паттернов в скомпилированные regex.
    
    Принимает:
    - Список строк: ["(Author) - Title", ...] 
    - Список объектов: [{"pattern": "(Author) - Title", "example": "..."}, ...]
    
    Возвращает список кортежей: (pattern_string, compiled_regex, group_names)
    
    Примеры:
    - ["(Author) - Title", "[Series] (Author)"] → 
      [
        ("(Author) - Title", regex_object, ['author', 'title']),
        ("[Series] (Author)", regex_object, ['series', 'author'])
      ]
    """
    if not pattern_strings:
        return []
    
    result = []
    for item in pattern_strings:
        try:
            # Если это объект с 'pattern', извлекаем паттерн
            if isinstance(item, dict):
                pattern_str = item.get('pattern', '').strip()
            else:
                # Если это строка
                pattern_str = str(item).strip()
            
            if not pattern_str:
                continue
            
            regex_str = convert_simple_pattern_to_regex(pattern_str)
            compiled_regex = re.compile(regex_str)
            group_names = extract_group_names(pattern_str)
            result.append((pattern_str, compiled_regex, group_names))
        except Exception as e:
            # Пропускаем невалидные паттерны
            continue
    
    return result
