"""
Block-Level Pattern Matching for Authors and Series Extraction.

Разбивает filename/pattern на БЛОКИ и сравнивает структуры для точного извлечения.

Blocks are structural elements separated by delimiters like:
- ' - ' (space-dash-space)
- '. ' (dot-space)
- '(' and ')' (parentheses)
- ',' (comma)

Example:
  Filename: "Янковский Дмитрий - Охотник (Тетралогия)"
  Blocks: ["Янковский Дмитрий", "Охотник", "(Тетралогия)"]
  
  Pattern: "Author - Title (Series.service_words)"
  Block-types: ["Author", "Title", "(Series)"]
  
  Match block 0 (filename) to block 0 (pattern) → "Янковский Дмитрий" is Author
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import re


@dataclass
class Block:
    """Structural block from text."""
    text: str              # Raw text of block
    block_type: str        # Type: "Author", "Title", "Series", "service_words", etc.
    position: int          # Position in block list
    is_parenthesized: bool # True if wrapped in ()
    delimiter: str = None  # Delimiter BEFORE this block (e.g., " - ", ". ", "(", ")")
    
    def __repr__(self):
        return f'Block("{self.text}", type={self.block_type}, delim={self.delimiter!r})'


class BlockLevelPatternMatcher:
    """Match filename against patterns using block-level structural comparison."""
    
    # Common Russian words that typically appear in titles, not author names
    TITLE_KEYWORDS = {
        'битва', 'война', 'король', 'королевство', 'императорь', 'империя',
        'мир', 'мира', 'небесный', 'звезда', 'звезд', 'звёзд', 'звёзда',
        'край', 'земля', 'земли', 'ночь', 'день', 'огонь', 'вода',
        'молния', 'гром', 'шторм', 'ураган', 'зима', 'лето', 'весна', 'осень',
        'город', 'замок', 'дворец', 'храм', 'церковь', 'святилище',
        'квест', 'поиск', 'охота', 'охотник', 'путешествие', 'путь',
        'магия', 'магический', 'чарованный', 'чародей', 'волшебник',
        'герой', 'герои', 'боец', 'боцена', 'воїн', 'война'
    }
    
    def __init__(self, service_words: List[str] = None, male_names: set = None, female_names: set = None):
        """Initialize matcher.
        
        Args:
            service_words: List of service words (series markers like "Тетралогия", "Дилогия")
            male_names: Set of known male first names for author validation (optional)
            female_names: Set of known female first names for author validation (optional)
        """
        self.service_words = set(w.lower() for w in (service_words or []))
        # Combine known author names for first-block validation
        self.known_author_names = set()
        if male_names:
            self.known_author_names.update(n.lower() for n in male_names)
        if female_names:
            self.known_author_names.update(n.lower() for n in female_names)
    
    def tokenize_filename(self, filename: str) -> List[Block]:
        """Break filename into structural blocks.
        
        Splits on delimiters according to unified principle:
        1. ' - ' (space-dash-space) → block separator
        2. '.' (dot, including '. ') → block separator
        3. '(' and ')' (parentheses) → block separators (content inside treated as parenthesized block)
        
        Block division is position-independent. All delimiters are treated uniformly.
        
        CRITICAL RULE: Text AFTER the LAST closing ')' is kept as a SINGLE BLOCK 
        (not further split by delimiters). This ensures proper pattern matching.
        
        Examples:
            "Янковский Дмитрий - Охотник (Тетралогия)" → 3 blocks
            "Мах. Квест империя" → 2 blocks
            "Посняков Андрей - Вещий князь (Вещий князь 1-4) Др. издание" → 4 blocks:
              1. "Посняков Андрей"
              2. "Вещий князь"
              3. "Вещий князь 1-4" (parenthesized)
              4. "Др. издание" (single block - NOT split further!)
        
        Args:
            filename: Filename string
            
        Returns:
            List of Block objects
        """
        if not filename:
            return []
        
        # Remove .fb2 if present
        text = filename.rstrip('.fb2') if filename.endswith('.fb2') else filename
        text = text.strip()
        
        # Find position of last closing parenthesis
        last_paren_pos = text.rfind(')')
        
        # If no parentheses, just split normally on delimiters
        if last_paren_pos == -1:
            return self._tokenize_with_delimiters(text)
        
        # Split into two parts: before/including last ')', and after
        before_last_paren = text[:last_paren_pos + 1]
        after_last_paren = text[last_paren_pos + 1:].strip()
        
        # Tokenize everything before and including last ')' with normal delimiter splitting
        blocks = self._tokenize_with_delimiters(before_last_paren)
        
        # Add text after last ')' as a SINGLE BLOCK (no further delimiter splitting!)
        if after_last_paren:
            blocks.append(Block(
                text=after_last_paren,
                block_type="text",
                position=len(blocks),
                is_parenthesized=False,
                delimiter=')'  # Delimiter before this block is the closing paren
            ))
        
        return blocks
    
    def _tokenize_with_delimiters(self, text: str) -> List[Block]:
        """Internal: tokenize text using unified delimiter pattern.
        
        Splits on ' - ', '.', '(', ')' uniformly.
        Tracks parenthesis depth to mark blocks.
        Stores delimiter information for each block.
        
        ВАЖНО: многоточие (..., .., ....) НЕ является разделителем и остается частью блока!
        Пример: "сдаюсь..." остается одним токеном, точки не разбивают его.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of Block objects
        """
        if not text:
            return []
        
        # PREPROCESSING: Replace ellipsis and guillemets with placeholders to protect from delimiter splitting
        # Ellipsis (2+ dots) and text in « » should stay together, not act as delimiters
        ELLIPSIS_PLACEHOLDER = "\x00ELLIPSIS\x00"  # Use null char as unlikely to appear in text
        
        # Store actual guillemets content for restoration
        guillemets_storage = {}
        def store_guillemets(match):
            """Store guillemets content and return placeholder."""
            placeholder = f"\x00GUILLEMETS_{len(guillemets_storage)}\x00"
            guillemets_storage[placeholder] = match.group(0)  # Store original with « »
            return placeholder
        
        text_processed = re.sub(r'\.{2,}', ELLIPSIS_PLACEHOLDER, text)  # Replace "..", "...", etc.
        text_processed = re.sub(r'«[^»]*»', store_guillemets, text_processed)  # Protect « ... »
        
        blocks = []
        # FIXED: Guillemets « » are NOT structural delimiters, they're formatting marks within text
        # Only treat actual parentheses () as structural delimiters, not guillemets
        # '\.\s+' требует пробел после точки, чтобы "." в "2.0" не был разделителем.
        # '(?!-)' — не сплитить по ". " когда следующий символ '-':
        # "Зан Т. - Траун" → не сплитить на ". " (инициал+точка), взять " - " как разделитель
        delimiter_pattern = r'\s+-\s+|\.\s+(?!-)|[()]'

        paren_depth = 0
        block_text_pos = 0
        prev_delimiter = None  # Track the delimiter before this block
        
        for match in re.finditer(delimiter_pattern, text_processed):
            delimiter = match.group()
            
            # IMPORTANT: Skip " - " and "." delimiters when inside parentheses
            # These are only valid delimiters at top level (paren_depth == 0)
            is_paren_delimiter = delimiter in ('(', ')')
            if paren_depth > 0 and not is_paren_delimiter:
                # This is a " - " or "." inside parens, so DON'T treat it as a delimiter
                # Skip this match and continue looking for the next one
                continue
            
            block_text = text_processed[block_text_pos:match.start()]
            block_text = block_text.strip()
            
            # POSTPROCESSING: Restore ellipsis and guillemets in block text
            block_text = block_text.replace(ELLIPSIS_PLACEHOLDER, "...")
            # Restore guillemets with actual content from storage
            for placeholder, original in guillemets_storage.items():
                block_text = block_text.replace(placeholder, original)
            
            # Add block if not empty
            if block_text:
                is_inside_parens = paren_depth > 0
                blocks.append(Block(
                    text=block_text,
                    block_type="text",
                    position=len(blocks),
                    is_parenthesized=is_inside_parens,
                    delimiter=prev_delimiter  # Store delimiter that came BEFORE this block
                ))
            
            # Update parenthesis depth based on current delimiter
            # Note: guillemets « » are NOT counted towards paren_depth (they're formatting, not structure)
            if delimiter == '(':
                paren_depth += 1
            elif delimiter == ')':
                paren_depth = max(0, paren_depth - 1)
            
            prev_delimiter = delimiter  # Next block will have this as its prefix delimiter
            block_text_pos = match.end()
        
        # Final block after last delimiter
        block_text = text_processed[block_text_pos:]
        block_text = block_text.strip()
        
        # POSTPROCESSING: Restore ellipsis and guillemets in final block text
        block_text = block_text.replace(ELLIPSIS_PLACEHOLDER, "...")
        # Restore guillemets with actual content from storage
        for placeholder, original in guillemets_storage.items():
            block_text = block_text.replace(placeholder, original)
        
        if block_text:
            is_inside_parens = paren_depth > 0
            blocks.append(Block(
                text=block_text,
                block_type="text",
                position=len(blocks),
                is_parenthesized=is_inside_parens,
                delimiter=prev_delimiter  # Last block's prefix delimiter
            ))
        
        return blocks
    
    def tokenize_pattern(self, pattern: str) -> List[Dict]:
        """Break pattern into structural block types.
        
        Uses same unified delimiter logic as tokenize_filename.
        Splits on ' - ', '.', '(', ')' uniformly.
        
        ВАЖНО: многоточие (..., .., ....) НЕ является разделителем!
        ВАЖНО: Текст в кавычках « » НЕ разбивается на блоки!
        Пример: "Серия..." остается одним типом, точки не разбивают.
        Пример: "Цикл «Я - лорд звездной империи»" остается одним блоком, дефис внутри не разбивает.
        
        Example:
            "Author - Title (Series. service_words)"
            → [
                {"type": "Author", "text": "Author", "position": 0, "parenthesized": False, "delimiter": None},
                {"type": "Title", "text": "Title", "position": 1, "parenthesized": False, "delimiter": " - "},
                {"type": "Series", "text": "Series", "position": 2, "parenthesized": True, "delimiter": "("}
              ]
        
        Args:
            pattern: Pattern string (e.g., "Author - Title (Series)")
            
        Returns:
            List of dicts with {type, text, position, parenthesized, delimiter}
        """
        if not pattern:
            return []
        
        # PREPROCESSING: Protect ellipsis and guillemets from being used as delimiters
        ELLIPSIS_PLACEHOLDER = "\x00ELLIPSIS\x00"
        
        # Store actual guillemets content for restoration
        guillemets_storage = {}
        def store_guillemets(match):
            """Store guillemets content and return placeholder."""
            placeholder = f"\x00GUILLEMETS_{len(guillemets_storage)}\x00"
            guillemets_storage[placeholder] = match.group(0)  # Store original with « »
            return placeholder
        
        pattern_processed = re.sub(r'\.{2,}', ELLIPSIS_PLACEHOLDER, pattern)
        pattern_processed = re.sub(r'«[^»]*»', store_guillemets, pattern_processed)  # Protect « ... »
        
        pattern_blocks = []
        
        # Split using same delimiter pattern as tokenize_filename
        # FIXED: Guillemets « » are NOT structural delimiters, they're formatting marks
        # '\.\s+' требует пробел после точки, чтобы "." в "2.0" не был разделителем.
        delimiter_pattern = r'\s+-\s+|\.\s+|[()]'
        
        paren_depth = 0
        block_text_pos = 0
        prev_delimiter = None
        
        for match in re.finditer(delimiter_pattern, pattern_processed):
            delimiter = match.group()
            
            # IMPORTANT: Skip " - " and "." delimiters when inside parentheses
            # These are only valid delimiters at top level (paren_depth == 0)
            is_paren_delimiter = delimiter in ('(', ')')
            if paren_depth > 0 and not is_paren_delimiter:
                # This is a " - " or "." inside parens, so DON'T treat it as a delimiter
                # Skip this match and continue looking for the next one
                continue
            
            block_text = pattern_processed[block_text_pos:match.start()]
            block_text = block_text.strip()
            
            # POSTPROCESSING: Restore ellipsis and guillemets
            block_text = block_text.replace(ELLIPSIS_PLACEHOLDER, "...")
            # Restore guillemets with actual content from storage
            for placeholder, original in guillemets_storage.items():
                block_text = block_text.replace(placeholder, original)
            
            # Add block if not empty
            if block_text:
                is_inside_parens = paren_depth > 0
                block_type = self._normalize_block_type(block_text)
                pattern_blocks.append({
                    "type": block_type,
                    "text": block_text,
                    "position": len(pattern_blocks),
                    "parenthesized": is_inside_parens,
                    "delimiter": prev_delimiter
                })
            
            # Update parenthesis depth
            # Note: guillemets « » are NOT counted towards paren_depth (they're formatting, not structure)
            if delimiter == '(':
                paren_depth += 1
            elif delimiter == ')':
                paren_depth = max(0, paren_depth - 1)
            
            prev_delimiter = delimiter
            block_text_pos = match.end()
        
        # Don't forget the last block
        block_text = pattern_processed[block_text_pos:]
        block_text = block_text.strip()
        
        # POSTPROCESSING: Restore ellipsis and guillemets
        block_text = block_text.replace(ELLIPSIS_PLACEHOLDER, "...")
        # Restore guillemets with actual content from storage
        for placeholder, original in guillemets_storage.items():
            block_text = block_text.replace(placeholder, original)
        if block_text:
            is_inside_parens = paren_depth > 0
            block_type = self._normalize_block_type(block_text)
            pattern_blocks.append({
                "type": block_type,
                "text": block_text,
                "position": len(pattern_blocks),
                "parenthesized": is_inside_parens,
                "delimiter": prev_delimiter
            })
        
        return pattern_blocks
    
    def _normalize_block_type(self, text: str) -> str:
        """Determine block type from pattern text.
        
        Recognizes:
        - "Author", "Author," → "Author"
        - "Title" → "Title"
        - "Series", "Series." → "Series"
        - "service_words" → "service_words"
        
        Args:
            text: Pattern text (e.g., "Author" or "Series. service_words")
            
        Returns:
            Normalized block type
        """
        text_lower = text.lower().strip(',. ')
        
        if 'author' in text_lower:
            return "Author"
        elif text_lower == 'subseries' or text_lower.startswith('subseries'):
            return "SubSeries"
        elif 'series' in text_lower:
            return "Series"
        elif 'title' in text_lower:
            return "Title"
        elif 'service_words' in text_lower:
            return "service_words"
        else:
            return "Title"  # Default to Title if unclear
    
    def score_pattern_match(self, filename: str, pattern: str) -> Tuple[float, Optional[str], Optional[str], Optional[str]]:
        """Score how well filename matches pattern structure.
        
        Returns: (score, pattern, matched_author_block, matched_series_block)
        
        Algorithm:
        1. Tokenize filename → list of blocks
        2. Tokenize pattern → list of block types
        3. Match blocks to block types
        4. Score based on:
           - Number of blocks matching
           - Types matching expected types
           - Service words detection
        5. Return highest score + extracted values
        
        Args:
            filename: Filename to match
            pattern: Pattern template
            
        Returns:
            (score_0_to_1, pattern, matched_author_block, matched_series_block, type_match_count)
        """
        filename_blocks = self.tokenize_filename(filename)
        pattern_blocks = self.tokenize_pattern(pattern)
        
        if not filename_blocks or not pattern_blocks:
            return 0.0, pattern, None, None
        
        # Hard rule: # of blocks must match
        if len(filename_blocks) != len(pattern_blocks):
            return 0.0, pattern, None, None

        # Hard rule: разделитель между автором (блок 0) и остальным текстом (блок 1)
        # должен совпадать — " - " и ". " несовместимы.
        if len(filename_blocks) >= 2 and len(pattern_blocks) >= 2:
            f_sep = (filename_blocks[1].delimiter or '').strip()
            p_sep = (pattern_blocks[1]['delimiter'] or '').strip()
            f_is_dash = f_sep == '-'
            p_is_dash = p_sep == '-'
            if f_is_dash != p_is_dash:
                return 0.0, pattern, None, None

        # CRITICAL: Check if first block contains known author names
        first_block_is_known_author = False
        if self.known_author_names and filename_blocks:
            first_block_words = set(w.lower() for w in filename_blocks[0].text.split())
            if first_block_words & self.known_author_names:
                first_block_is_known_author = True
        
        # Score each block match
        score = 0.0
        max_score = 0.0
        author_block = None
        series_blocks = []     # Collect Series blocks
        subseries_blocks = []  # Collect SubSeries blocks (joined with \ into hierarchy)
        type_match_count = 0   # Count how many blocks had correct type match
        
        for position, (fname_block, pblock) in enumerate(zip(filename_blocks, pattern_blocks)):
            max_score += 1.0 + 0.1  # 1.0 for type match, 0.1 for potential delimiter bonus

            # Hard rule: parenthesization must match exactly
            # If pattern expects block in parentheses but filename has none (or vice versa) — reject
            if fname_block.is_parenthesized != pblock['parenthesized']:
                return 0.0, pattern, None, None

            # Hard rule: guillemets «» must match exactly
            # If pattern has «Series» but filename block has no guillemets — reject
            pattern_has_guillemets = '«' in pblock['text']
            filename_has_guillemets = '«' in fname_block.text
            if pattern_has_guillemets != filename_has_guillemets:
                return 0.0, pattern, None, None

            score += 0.5  # Structural markers (parenthesization + guillemets) match
            
            # Match block type expectation
            expected_type = pblock['type']
            
            # PENALTY: If first block contains known author name but pattern expects Title at pos 0
            if position == 0 and first_block_is_known_author and expected_type == "Title":
                # Pattern structure is WRONG for this filename
                # Pattern like "Title - Author" makes no sense when first block is a known author
                score -= 0.5  # Apply penalty
            
            # CRITICAL: For FIRST block (position==0) expecting "Author",
            # check if it's in known author names BEFORE using _guess_block_type()
            if position == 0 and expected_type == "Author" and self.known_author_names:
                # Check if ANY word in block matches known author names
                block_words = set(w.lower() for w in fname_block.text.split())
                matches = block_words & self.known_author_names  # Set intersection
                if matches:
                    fname_type = "Author"  # Confident match from known names
                else:
                    fname_type = self._guess_block_type(fname_block.text)
            else:
                fname_type = self._guess_block_type(fname_block.text)
            
            # Context-aware type adjustment: Series names often look like book titles.
            # If pattern expects Series/SubSeries at this position and block looks like Title → accept.
            if expected_type in ("Series", "SubSeries") and fname_type == "Title":
                fname_type = expected_type
            # Series names often look like Author names (proper nouns, e.g. "Траун").
            # If past position 0 and pattern expects Series but guessed Author — accept.
            if position > 0 and expected_type == "Series" and fname_type == "Author":
                fname_type = "Series"
            # SubSeries often contains a trailing number ("Доминация 1") → _guess returns "Series". Accept.
            if expected_type == "SubSeries" and fname_type == "Series":
                fname_type = "SubSeries"
            # Also: after service_words block, force Series (original logic kept as fallback)
            if (position > 0 and expected_type == "Series" and
                pattern_blocks[position - 1]['type'] == "service_words" and
                fname_type == "Author"):  # Only override Author guesses now (Title already covered above)
                fname_type = "Series"
            
            # Check delimiter match
            delimiter_match = fname_block.delimiter == pblock['delimiter']

            if fname_type == expected_type:
                score += 0.5  # Type matches!
                
                # Bonus: if delimiters also match, add small bonus
                if delimiter_match:
                    score += 0.1  # Delimiter match is strong signal
                
                type_match_count += 1  # Track for tie-breaking
                
                # Track which block is Author/Series
                if expected_type == "Author":
                    author_block = fname_block.text
                elif expected_type == "Series":
                    series_blocks.append(fname_block.text)
                elif expected_type == "SubSeries":
                    subseries_blocks.append(fname_block.text)

        
        # Reconstruct series hierarchy:
        # - Series blocks joined with '. ' (flat multi-word series name)
        # - SubSeries blocks appended via '\' (hierarchical subseries)
        # Example: Series="Цена победы", SubSeries="Горе победителям"
        #          → "Цена победы\Горе победителям"
        base_series = '. '.join(series_blocks) if series_blocks else None
        if subseries_blocks:
            sub_part = '\\'.join(subseries_blocks)
            series_block = f'{base_series}\\{sub_part}' if base_series else sub_part
        else:
            series_block = base_series
        
        normalized_score = score / max_score if max_score > 0 else 0.0
        # Store type_match_count as attribute on return value for tie-breaking
        self._last_type_match_count = type_match_count
        return normalized_score, pattern, author_block, series_block
    
    def _guess_block_type(self, block_text: str) -> str:
        """Guess what type a block is based on content.
        
        Heuristics:
        - Contains service words (Тетралогия, Дилогия) → Series
        - Contains parenthesized numbers (1-3, 2) → Series
        - Contains known names/surnames → Author
        - Contains title keywords → Title
        - Otherwise → Title
        
        Args:
            block_text: Text of the block
            
        Returns:
            Guessed type: "Author", "Series", or "Title"
        """
        text_lower = block_text.lower()

        # SW qualifiers: words that signal service_words ONLY when combined with a real SW.
        # NOT added to self.service_words because standalone they can start a Title.
        # "Весь мир передо мной" — "весь" alone → Title; "весь цикл" → service_words.
        SW_QUALIFIERS = {'весь', 'вся', 'все', 'полный', 'полная', 'полное',
                         'целый', 'целая', 'целое', 'complete', 'omnibus'}

        # Check for service words (series markers)
        for word in self.service_words:
            if word in text_lower:
                block_words = text_lower.split()
                # All tokens are SW, numbers, or SW-qualifiers → pure numbering/annotation block
                # e.g. "1 часть", "весь цикл", "вся трилогия", "книга 3"
                if all(w in self.service_words or re.match(r'^\d+$', w) or w in SW_QUALIFIERS
                       for w in block_words):
                    return "service_words"
                else:
                    # Contains service word among other text → Series label
                    return "Series"
        
        # Check for number patterns (1-3, 1, vol. 2, etc.)
        if re.search(r'\d+[-–—]\d+|\b\d+$|\bvol\.\s+\d+', block_text):
            return "Series"
        
        # Check for title keywords - if present, likely Title, not Author
        text_words = set(text_lower.split())
        if any(word in self.TITLE_KEYWORDS for word in text_words):
            return "Title"
        
        # Check if looks like author (simple heuristic: has 2+ words, Cyrillic)
        words = block_text.split()
        if len(words) >= 2:
            # Likely "Surname Name" format - BUT only if it looks like actual names
            # Series titles often have multiple words too, so be more strict
            # Real author names typically: 
            # - Have capitalized first letters (Иван Петров)
            # - Don't have complex verb patterns (солдат удачи - soldier of luck)
            # Check if all words start with capital or are very short (particles)
            looks_like_names = all(
                (w[0].isupper() or len(w) <= 2) 
                for w in words[:2] 
                if self._is_cyrillic_word(w)
            )
            if looks_like_names and all(self._is_cyrillic_word(w) for w in words[:2]):
                return "Author"
        
        # Single Russian word of 3+ chars (relaxed) → likely surname
        if len(words) == 1 and len(block_text) >= 3:
            if self._is_cyrillic_word(block_text):
                # Additional check: make sure it's not a common title keyword
                if text_lower not in self.TITLE_KEYWORDS:
                    return "Author"
        
        return "Title"
    
    def _is_cyrillic_word(self, word: str) -> bool:
        """Check if word is Cyrillic."""
        return any('\u0400' <= c <= '\u04FF' for c in word)
    
    def find_best_pattern_match(self, filename: str, patterns: List[Dict]) -> Tuple[float, str, Optional[str], Optional[str]]:
        """Find best matching pattern for filename and extract Author/Series.
        
        Uses scoring with tie-breaking:
        1. Primary: pattern match score (0-1)
        2. Secondary: number of blocks with correct type match
        
        Args:
            filename: Filename to match
            patterns: List of pattern dicts with 'pattern' key
            
        Returns:
            (best_score, best_pattern, extracted_author, extracted_series)
        """
        best_score = 0.0
        best_pattern = None
        best_author = None
        best_series = None
        best_type_matches = 0  # Tie-breaker: count of blocks with correct type
        
        for pattern_obj in patterns:
            pattern = pattern_obj.get('pattern', '')
            score, matched_pattern, author, series = self.score_pattern_match(filename, pattern)
            
            # Primary check: higher score
            if score > best_score:
                best_score = score
                best_pattern = matched_pattern
                best_author = author
                best_series = series
                best_type_matches = getattr(self, '_last_type_match_count', 0)
            # Tie-breaking: if same score, prefer more type matches
            elif score == best_score and score > 0.0:
                current_type_matches = getattr(self, '_last_type_match_count', 0)
                if current_type_matches > best_type_matches:
                    best_pattern = matched_pattern
                    best_author = author
                    best_series = series
                    best_type_matches = current_type_matches
        
        return best_score, best_pattern, best_author, best_series


__all__ = [
    'Block',
    'BlockLevelPatternMatcher',
]
