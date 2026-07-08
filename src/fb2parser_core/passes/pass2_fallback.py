"""
PASS 2 Fallback: Apply metadata as last resort for records without author.
Detects collections when 3+ authors present and filename contains collection keywords.
"""

import os
from typing import List
from ..settings_manager import SettingsManager


class Pass2Fallback:
    """PASS 2 Fallback: Use metadata as last resort for author assignment.
    
    Also detects collection/anthology files when:
    - metadata_authors contains 3+ authors
    - filename contains collection keywords from config
    """
    
    def __init__(self, logger, settings=None):
        """Initialize PASS 2 Fallback.
        
        Args:
            logger: Logger instance
            settings: Optional shared SettingsManager (avoids extra config.json read)
        """
        self.logger = logger
        try:
            self.settings = settings or SettingsManager('config.json')
            self.collection_keywords = self.settings.get_list('collection_keywords') or []
        except Exception as e:
            print(f"[PASS 2 Fallback] Warning: Could not load collection_keywords: {e}")
            self.collection_keywords = []
    
    def _is_collection_file(self, filename: str) -> bool:
        """Check if filename contains collection keywords.
        
        Args:
            filename: File name to check
            
        Returns:
            True if filename contains any collection keyword, False otherwise
        """
        if not self.collection_keywords or not filename:
            return False
        
        filename_lower = filename.lower()
        for keyword in self.collection_keywords:
            if keyword.lower() in filename_lower:
                return True
        return False
    
    def _count_authors(self, authors_str: str) -> int:
        """Count number of authors in authors string.
        
        Authors are separated by "; " or ", "
        
        Args:
            authors_str: String with authors
            
        Returns:
            Number of authors
        """
        if not authors_str or authors_str == "[unknown]":
            return 0
        
        # Count authors separated by "; " or ", "
        if "; " in authors_str:
            return len([a for a in authors_str.split("; ") if a.strip()])
        elif ", " in authors_str:
            return len([a for a in authors_str.split(", ") if a.strip()])
        else:
            return 1 if authors_str.strip() else 0
    
    def execute(self, records: List) -> None:
        """Execute PASS 2 Fallback: Apply metadata for records without author.
        
        For records with empty proposed_author after PASS 1 and PASS 2:
        1. First check if this is a collection file (3+ metadata authors + collection keywords)
        2. If collection, mark as "Сборник"
        3. Otherwise, apply metadata_authors as last resort
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 2 Fallback] Applying metadata as last resort...")
        
        fallback_count = 0
        collection_count = 0
        
        for record in records:
            # Only for records without determined author
            if record.proposed_author:
                continue
            
            # Check for collection: 3+ metadata authors + collection keywords in filename
            author_count = self._count_authors(record.metadata_authors)
            filename = os.path.basename(record.file_path) if record.file_path else ""
            is_collection = self._is_collection_file(filename)
            
            if author_count >= 3 and is_collection:
                # This is a collection/anthology
                record.proposed_author = "Сборник"
                record.author_source = "collection"
                collection_count += 1
            # Apply metadata
            elif record.metadata_authors and record.metadata_authors != "[unknown]":
                record.proposed_author = record.metadata_authors
                record.author_source = "metadata"
                fallback_count += 1
            else:
                # Even metadata is empty
                record.proposed_author = ""
                record.author_source = ""
        
        self.logger.log(f"[PASS 2 Fallback] Applied metadata to {fallback_count} records, "
                       f"detected {collection_count} collection files")
