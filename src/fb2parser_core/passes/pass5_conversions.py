"""
PASS 5: Re-apply author surname conversions.
"""

from typing import List, Optional
from ..author_normalizer_extended import AuthorNormalizer
from ..settings_manager import SettingsManager


class Pass5Conversions:
    """PASS 5: Re-apply author surname conversions after consensus.
    
    Apply surname conversions a second time since PASS 4 consensus may have
    changed the author, and the new author may need conversion.
    
    Examples of conversions:
    - "Старец" → "Старицын"
    - "Сезин" → "Сезин" (no change, but checked)
    """
    
    def __init__(self, logger, settings=None):
        """Initialize PASS 5.
        
        Args:
            logger: Logger instance
            settings: Optional shared SettingsManager
        """
        self.logger = logger
        try:
            self.settings = settings or SettingsManager('config.json')
        except:
            self.settings = None
        self.normalizer = AuthorNormalizer(self.settings)
    
    def execute(self, records: List) -> None:
        """Execute PASS 5: Re-apply surname conversions.
        
        Apply surname conversions to all authors (single and multi-author).
        Handles both separators: '; ' and ', '.
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 5] Re-applying conversions...")
        
        conversions_count = 0
        
        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue

            original = record.proposed_author
            
            # Check for multi-author case with both separators
            if '; ' in record.proposed_author:
                authors = record.proposed_author.split('; ')
                converted_authors = [self.normalizer.apply_conversions(a) for a in authors]
                record.proposed_author = '; '.join(converted_authors)
            elif ', ' in record.proposed_author:
                authors = record.proposed_author.split(', ')
                converted_authors = [self.normalizer.apply_conversions(a) for a in authors]
                record.proposed_author = ', '.join(converted_authors)
            else:
                record.proposed_author = self.normalizer.apply_conversions(original)
            
            if record.proposed_author != original:
                conversions_count += 1
        
        self.logger.log(f"[PASS 5] Applied conversions to {conversions_count} records")
