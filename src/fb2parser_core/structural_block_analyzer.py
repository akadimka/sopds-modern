#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Анализ выбора паттерна on основе структурных блоков
Показывает почему specific paттерн выбран
"""

import re
from typing import List, Dict, Tuple

class BlockLevelPatternSelector:
    """Выбирает паттерн на основе блочного анализа файла и паттернов"""
    
    def __init__(self, service_words: List[str] = None):
        self.service_words = service_words or []
    
    def analyze_filename_parts(self, filename: str) -> Dict:
        """Разбирает файл на основные блоки"""
        
        # Блок 1: До скобок
        bracket_match = re.search(r'\(([^)]+)\)\s*$', filename)
        if bracket_match:
            before_brackets = filename[:bracket_match.start()].strip()
            content_in_brackets = bracket_match.group(1).strip()
        else:
            before_brackets = filename
            content_in_brackets = None
        
        # Анализируем "before_brackets" часть
        before_analysis = {
            'text': before_brackets,
            'has_comma': ',' in before_brackets,
            'comma_count': before_brackets.count(','),
            'has_dot': '.' in before_brackets,
            'has_dash': ' - ' in before_brackets,
        }
        
        # Анализируем содержимое скобок
        bracket_analysis = {}
        if content_in_brackets:
            bracket_analysis = self.analyze_bracket_content(content_in_brackets)
        
        return {
            'filename': filename,
            'before_brackets': before_analysis,
            'in_brackets': bracket_analysis,
        }
    
    def analyze_bracket_content(self, bracket_content: str) -> Dict:
        """Анализирует что находится в скобках"""
        
        content_lower = bracket_content.lower()
        
        analysis = {
            'content': bracket_content,
            'has_service_word': False,
            'service_words_found': [],
            'has_numeric_range': bool(re.search(r'\d+-\d+', bracket_content)),
            'has_dot': '.' in bracket_content,
            'parts_by_dot': bracket_content.split('.') if '.' in bracket_content else [bracket_content],
            'num_parts': len(bracket_content.split('.')) if '.' in bracket_content else 1,
        }
        
        # Проверяем служебные слова
        for service_word in self.service_words:
            if service_word.lower() in content_lower:
                analysis['has_service_word'] = True
                analysis['service_words_found'].append(service_word)
        
        return analysis
    
    def analyze_pattern_requirements(self, pattern: str) -> Dict:
        """Анализирует что требует паттерн"""
        
        # Извлекаем раздел со скобками
        if '(' in pattern and ')' in pattern:
            bracket_section = pattern[pattern.find('('):]
        else:
            bracket_section = None
        
        requirements = {
            'pattern': pattern,
            'requires_comma': ',' in pattern,
            'requires_dot': '. ' in pattern,
            'requires_dash': ' - ' in pattern,
            'requires_brackets': '(' in pattern and ')' in pattern,
            'requires_two_authors': pattern.count('Author') >= 2,
        }
        
        # Анализируем что требуется в скобках
        if bracket_section:
            requirements['bracket_requirements'] = self.analyze_bracket_requirements(bracket_section)
        
        return requirements
    
    def analyze_bracket_requirements(self, bracket_section: str) -> Dict:
        """Анализирует требования к содержимому скобок"""
        
        return {
            'section': bracket_section,
            'requires_service_words': 'service_words' in bracket_section,
            'requires_series_only': 'Series' in bracket_section and 'service_words' not in bracket_section and bracket_section.count('.') == 0,
            'requires_complex_structure': bracket_section.count('.') > 0 or bracket_section.count('Series') > 1,
            'structure_complexity': bracket_section.count('.') + 1,  # 0 dots = 1 level, 1 dot = 2 levels, etc.
        }
    
    def compare_blocks(self, filename_parts: Dict, pattern_requirements: Dict) -> Tuple[int, List[str]]:
        """
        Сравнивает блоки файла с требованиями паттерна.
        
        Возвращает: (score, list_of_reasons)
        """
        
        reasons = []
        score = 0
        
        # ══════ ЧАСТЬ ДО СКОБОК ══════
        before = filename_parts['before_brackets']
        
        # Запятая (соавторы)
        if pattern_requirements['requires_comma']:
            if before['has_comma']:
                score += 10
                reasons.append("✅ Паттерн требует запятую (соавторы) - ЕСТЬ в файле")
            else:
                return (-999, ["❌ Паттерн требует запятую, но её нет в файле"])
        else:
            if not before['has_comma']:
                score += 10
                reasons.append("✅ Паттерн не требует запятую - в файле нет запятой")
            else:
                score -= 5
                reasons.append("⚠️  Паттерн не требует запятую, но она ЕСТЬ в файле (неточное совпадение)")
        
        # Точка
        if pattern_requirements['requires_dot']:
            if before['has_dot']:
                score += 10
                reasons.append("✅ Паттерн требует точку - ЕСТЬ в файле")
            else:
                return (-999, ["❌ Паттерн требует точку, но её нет в файле"])
        else:
            if not before['has_dot']:
                score += 10
                reasons.append("✅ Паттерн не требует точку - в файле нет точки")
            else:
                score -= 5
                reasons.append("⚠️  Паттерн не требует точку, но она ЕСТЬ в файле")
        
        # Тире
        if pattern_requirements['requires_dash']:
            if before['has_dash']:
                score += 10
                reasons.append("✅ Паттерн требует тире - ЕСТЬ в файле")
            else:
                return (-999, ["❌ Паттерн требует тире, но его нет в файле"])
        else:
            if not before['has_dash']:
                score += 10
                reasons.append("✅ Паттерн не требует тире - в файле нет тире")
        
        # ══════ ЧАСТЬ В СКОБКАХ ══════
        if pattern_requirements['requires_brackets']:
            if not filename_parts.get('in_brackets'):
                return (-999, ["❌ Паттерн требует скобки, но их нет в файле"])
            
            in_brackets_file = filename_parts['in_brackets']
            bracket_reqs = pattern_requirements.get('bracket_requirements', {})
            
            score += 15
            reasons.append("✅ Паттерн требует скобки - ЕСТЬ в файле")
            
            # ════ Проверяем содержимое скобок ════
            
            if bracket_reqs.get('requires_series_only'):
                # Паттерн требует ТОЛЬКО Series без service_words
                # Например: "Author, Author. Title (Series)"
                
                if in_brackets_file['has_service_word']:
                    # В файле есть служебное слово, но паттерн не одна ожидает
                    score -= 3
                    reasons.append(f"⚠️  Паттерн требует Series only (без service_words), но в файле найдено: {in_brackets_file['service_words_found']}")
                else:
                    # В файле НЕТ служебного слова, паттерн это НЕ требует
                    score += 5
                    reasons.append(f"✅ Паттерн требует Series only - в скобках ТОЛЬКО series: '{in_brackets_file['content']}'")
            
            elif bracket_reqs.get('requires_service_words'):
                # Паттерн требует service_words
                # Например: "Author, Author. Title (Series service_words)"
                
                if not in_brackets_file['has_service_word']:
                    # Паттерн требует service_words, но их нет в файле
                    score -= 5
                    reasons.append(f"⚠️  Паттерн требует service_words, но в скобках только: '{in_brackets_file['content']}'")
                else:
                    score += 5
                    reasons.append(f"✅ Паттерн требует service_words - найдены: {in_brackets_file['service_words_found']}")
            
            # Сложность структуры в скобках
            pattern_complexity = bracket_reqs.get('structure_complexity', 1)
            file_complexity = in_brackets_file.get('num_parts', 1)
            
            if pattern_complexity != file_complexity:
                penalty = abs(pattern_complexity - file_complexity) * 2
                score -= penalty
                reasons.append(f"⚠️  Паттерн требует {pattern_complexity} уровней в скобках, файл имеет {file_complexity}")
        
        return (score, reasons)
    
    def select_best_pattern(self, filename: str, patterns: List[str]) -> Tuple[str, int, List[str]]:
        """Выбирает лучший паттерн и объясняет почему"""
        
        filename_parts = self.analyze_filename_parts(filename)
        
        best_pattern = None
        best_score = -999
        best_reasons = []
        
        for pattern in patterns:
            pattern_reqs = self.analyze_pattern_requirements(pattern)
            score, reasons = self.compare_blocks(filename_parts, pattern_reqs)
            
            if score > best_score:
                best_score = score
                best_pattern = pattern
                best_reasons = reasons
        
        return best_pattern, best_score, best_reasons


# ТЕСТ
if __name__ == '__main__':
    service_words = ['Дилогия', 'Трилогия', 'Тетралогия', 'Пенталогия', 'Цикл', 'Серия']
    
    selector = BlockLevelPatternSelector(service_words)
    
    filename = "Зурков, Черепнев. Бешеный прапорщик (Бешеный прапорщик 1-3)"
    patterns = [
        "Author, Author. Title (Series)",
        "Author, Author. Title (Series service_words)", 
        "Author, Author. Title (Series. Title. service_words)",
    ]
    
    print("=" * 120)
    print("БЛОЧНЫЙ АНАЛИЗ ВЫБОРА ПАТТЕРНА")
    print("=" * 120)
    print(f"\nФайл: {filename}\n")
    
    # Анализируем файл
    filename_parts = selector.analyze_filename_parts(filename)
    print("📄 АНАЛИЗ ФАЙЛА:")
    print(f"  Before brackets: {filename_parts['before_brackets']}")
    print(f"  In brackets: {filename_parts['in_brackets']}")
    
    print("\n" + "=" * 120)
    print("СРАВНЕНИЕ С ПАТТЕРНАМИ:")
    print("=" * 120)
    
    results = []
    for pattern in patterns:
        pattern_reqs = selector.analyze_pattern_requirements(pattern)
        score, reasons = selector.compare_blocks(filename_parts, pattern_reqs)
        results.append((score, pattern, reasons))
        
        print(f"\n📋 {pattern}")
        print(f"   Score: {score}")
        print(f"   Requirements: {pattern_reqs}")
        for reason in reasons:
            print(f"   {reason}")
    
    # Сортируем по score
    results.sort(reverse=True)
    
    print("\n" + "=" * 120)
    print("ИТОГОВЫЙ РЕЗУЛЬТАТ:")
    print("=" * 120)
    print(f"\n🏆 ЛУЧШИЙ ПАТТЕРН: {results[0][1]}")
    print(f"   Score: {results[0][0]}")
    print(f"\n   Причины:")
    for reason in results[0][2]:
        print(f"   {reason}")
