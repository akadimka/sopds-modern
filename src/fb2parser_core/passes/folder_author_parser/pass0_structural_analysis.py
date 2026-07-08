"""
PASS0: Structural Analysis of folder name

Identifies:
- Bracket positions and content
- Comma presence
- Dash with spaces presence
- Text before first bracket / after last bracket
"""

from typing import Tuple, List


def analyze_structure(folder_name: str) -> dict:
    """
    Performs structural analysis on folder name.
    
    Returns:
        dict with keys:
            - paren_count: Number of parentheses
            - paren_positions: List of (start, end, content) tuples
            - paren_contents: List of content inside parentheses
            - bracket_positioning: 'none' | 'start' | 'end' | 'middle' | 'wrap' | 'multiple'
            - text_before_first: Text before first bracket
            - text_after_last: Text after last bracket
            - has_comma: True if comma exists in name
            - has_comma_in_parens: True if comma in any parentheses
            - has_dash_with_spaces: True if ' - ' exists
    """
    
    name = folder_name.strip()
    
    # ==================== Find all parentheses ====================
    paren_positions: List[Tuple[int, int, str]] = []
    paren_contents: List[str] = []
    
    i = 0
    while i < len(name):
        if name[i] == '(':
            j = i + 1
            while j < len(name) and name[j] != ')':
                j += 1
            if j < len(name):  # Found closing bracket
                content = name[i+1:j]
                paren_positions.append((i, j+1, content))
                paren_contents.append(content)
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    
    paren_count = len(paren_contents)
    
    # ==================== Determine bracket positioning ====================
    bracket_positioning = 'none'
    if paren_count > 0:
        first_paren_start = paren_positions[0][0]
        last_paren_end = paren_positions[-1][1]
        
        if paren_count == 1:
            if first_paren_start == 0:
                bracket_positioning = 'start'
            elif last_paren_end == len(name):
                bracket_positioning = 'end'
            else:
                bracket_positioning = 'middle'
        else:
            if first_paren_start == 0 and last_paren_end == len(name):
                bracket_positioning = 'wrap'
            else:
                bracket_positioning = 'multiple'
    
    # ==================== Extract text before/after brackets ====================
    text_before_first = ""
    text_after_last = ""
    
    if paren_count > 0:
        text_before_first = name[:paren_positions[0][0]].strip()
        text_after_last = name[paren_positions[-1][1]:].strip()
    
    # ==================== Check for comma and dash ====================
    has_comma = ',' in name
    has_comma_in_parens = any(',' in content for content in paren_contents)
    has_dash_with_spaces = ' - ' in name
    
    return {
        'paren_count': paren_count,
        'paren_positions': paren_positions,
        'paren_contents': paren_contents,
        'bracket_positioning': bracket_positioning,
        'text_before_first': text_before_first,
        'text_after_last': text_after_last,
        'has_comma': has_comma,
        'has_comma_in_parens': has_comma_in_parens,
        'has_dash_with_spaces': has_dash_with_spaces,
        'name': name,
    }
