"""
File Structural Analysis Module

Analyzes filename structure to match against author/series patterns.
Provides structural information about how a filename is organized (brackets, dashes, dots, etc.)
and scores how well it matches a given pattern template.
"""

from typing import Dict, List, Tuple
import re


def analyze_file_structure(filename: str, service_words: List[str] = None) -> Dict:
    """
    Analyzes the structure of a filename.
    
    Identifies:
    - Bracket positions and content
    - Dash and dot positions
    - Comma presence
    - Text segments
    
    Args:
        filename: The filename to analyze (without path)
        service_words: List of service words to skip (optional)
    
    Returns:
        Dictionary with structural analysis results:
            - segments: List of text segments separated by major delimiters
            - paren_count: Number of parentheses
            - paren_contents: List of content inside parentheses
            - bracket_positioning: 'none', 'start', 'end', 'middle', or 'wrap'
            - text_before_first_paren: Text before first bracket
            - text_after_last_paren: Text after last bracket
            - has_comma: True if comma exists
            - has_comma_in_parens: True if comma in parentheses
            - has_dash: True if dash (with spaces) exists
            - has_single_dash: True if single dash inside parentheses
            - original: Original filename
    """
    
    name = filename.strip()
    if name.lower().endswith('.fb2.zip'):
        name = name[:-8]
    elif name.lower().endswith('.fb2'):
        name = name[:-4]
    
    # Find all parentheses
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
    
    # Determine bracket positioning
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
    
    # Extract text before/after brackets
    text_before_first_paren = ""
    text_after_last_paren = ""
    
    if paren_count > 0:
        text_before_first_paren = name[:paren_positions[0][0]].strip()
        text_after_last_paren = name[paren_positions[-1][1]:].strip()
    
    # Check for delimiters
    has_comma = ',' in name
    has_comma_in_parens = any(',' in content for content in paren_contents)
    has_dash = ' - ' in name
    has_single_dash = any('-' in content and content.count('-') == 1 for content in paren_contents)
    
    # Segment the filename by major delimiters
    segments = _segment_filename(name, paren_positions)
    
    return {
        'segments': segments,
        'paren_count': paren_count,
        'paren_contents': paren_contents,
        'paren_positions': paren_positions,
        'bracket_positioning': bracket_positioning,
        'text_before_first_paren': text_before_first_paren,
        'text_after_last_paren': text_after_last_paren,
        'has_comma': has_comma,
        'has_comma_in_parens': has_comma_in_parens,
        'has_dash': has_dash,
        'has_single_dash': has_single_dash,
        'original': filename,
    }


def _segment_filename(name: str, paren_positions: List[Tuple[int, int, str]]) -> List[str]:
    """
    Segments filename into logical parts based on delimiters.
    
    Returns list of non-empty text segments.
    """
    segments = []
    
    # Split by major delimiters: ' - ', '.', ','
    # But respect bracket boundaries
    current = ""
    i = 0
    
    while i < len(name):
        # Check if we're at a bracket
        in_bracket = False
        for start, end, _ in paren_positions:
            if i == start:
                # Add current segment
                if current.strip():
                    segments.append(current.strip())
                current = ""
                # Skip to end of bracket
                i = end
                in_bracket = True
                break
        
        if in_bracket:
            continue
        
        # Check for delimiters
        if i + 2 < len(name) and name[i:i+3] == ' - ':
            if current.strip():
                segments.append(current.strip())
            current = ""
            i += 3
        elif name[i] in '.,' and i + 1 < len(name) and name[i + 1] == ' ':
            if current.strip():
                segments.append(current.strip())
            current = ""
            i += 2
        else:
            current += name[i]
            i += 1
    
    if current.strip():
        segments.append(current.strip())
    
    return segments


def score_pattern_match(struct: Dict, pattern: str, service_words: List[str] = None) -> float:
    """
    Scores how well the file structure matches a given pattern.
    
    Pattern syntax:
    - "Author" - text segment
    - "(Author)" - text in parentheses
    - "Title" - filename text
    - "-" - space-dash-space separator
    - "." - dot separator
    - "service_words" - service words like "Тетралогия", "part", etc.
    
    Returns a score from 0.0 to 1.0 where:
    - 1.0 = perfect match
    - 0.0 = no match
    
    Args:
        struct: Structure dict from analyze_file_structure()
        pattern: Pattern string from config (e.g., "(Author) - Title")
        service_words: List of service words (optional)
    
    Returns:
        Float score 0.0-1.0
    """
    
    if service_words is None:
        service_words = []
    
    pattern_lower = pattern.lower()

    # ── HARD DISQUALIFIERS ──────────────────────────────────────────────────
    # If the pattern REQUIRES a structural element that the filename LACKS,
    # this pattern cannot possibly match → return 0.0 immediately.

    # Pattern requires ' - ' (dash separator) but filename has none
    if ' - ' in pattern and not struct['has_dash']:
        return 0.0

    # Pattern requires ',' (co-author comma) but filename has none
    if ',' in pattern and not struct['has_comma']:
        return 0.0

    # Pattern requires parentheses '(' but filename has none
    if '(' in pattern and struct['paren_count'] == 0:
        return 0.0

    # Pattern requires '(author)' bracket but filename has no brackets at all
    if '(author)' in pattern_lower and struct['bracket_positioning'] not in ['start', 'end', 'middle', 'wrap']:
        return 0.0

    # ── POSITIVE SCORING ────────────────────────────────────────────────────
    # Award points for each structural element the pattern correctly accounts for.
    # Also award points when pattern correctly predicts ABSENCE of an element.

    score = 0.0
    max_score = 0.0

    # Dash
    max_score += 1.0
    if ' - ' in pattern:
        if struct['has_dash']:
            score += 1.0
    else:
        # Pattern has no dash — reward if filename also has no dash
        if not struct['has_dash']:
            score += 1.0
        # else: filename has dash but pattern ignores it → no reward (soft penalty)

    # Comma (co-authors)
    max_score += 1.0
    if ',' in pattern:
        if struct['has_comma']:
            score += 1.0
    else:
        # Pattern has no comma — reward if filename also has no comma
        if not struct['has_comma']:
            score += 1.0
        # else: filename has comma but pattern ignores it → no reward (soft penalty)

    # Parentheses
    max_score += 1.0
    if '(' in pattern:
        if struct['paren_count'] > 0:
            score += 1.0
    else:
        # Pattern has no parens — reward if filename also has no parens
        if struct['paren_count'] == 0:
            score += 1.0
        # else: filename has parens but pattern ignores them → no reward

    # Dot separator (outside parens)
    has_dot_outside_parens = ('.' in struct['original'].split('(')[0]
                              if '(' in struct['original']
                              else '.' in struct['original'])
    pattern_has_dot = '.' in pattern_lower.replace('(series)', '').replace('(author)', '')
    max_score += 1.0
    if pattern_has_dot:
        if has_dot_outside_parens:
            score += 1.0
    else:
        if not has_dot_outside_parens:
            score += 1.0

    # Base: filename has at least one segment (always true for non-empty filenames)
    if struct['segments']:
        max_score += 1.0
        score += 1.0

    # Normalize score
    if max_score == 0:
        return 0.0

    return min(1.0, score / max_score)


__all__ = [
    'analyze_file_structure',
    'score_pattern_match',
]
