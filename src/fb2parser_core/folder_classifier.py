"""
Folder Classifier — determines the semantic type of a folder in the library hierarchy.

Priority of classification (cascade, first match wins):
  1. SKIP       — technical/system folder names (fb2, tmp, covers, …)
  2. PUBLISHER  — starts with known publisher prefix ("Серия - «", …)
                  OR contains a blacklisted genre/publisher word
  3. COLLECTION — contains a collection keyword (сборник, антология, …)
  4. VARIANT    — contains a variant/edition keyword (ЛП, СИ, черновик, …)
  5. NO_SERIES  — exact match with a "no series" folder name (Вне серий, …)
  6. AUTHOR     — at least one word matches male_names or female_names from config
  7. UNKNOWN    — nothing matched; treat as a potential series folder

Usage:
    classifier = FolderClassifier(settings)
    folder_type = classifier.classify("Волков Тим")          # → FolderType.AUTHOR
    folder_type = classifier.classify("Серия - «Боевая»")    # → FolderType.PUBLISHER
    folder_type = classifier.classify("Дуэлянт")             # → FolderType.UNKNOWN
"""

import re
from enum import Enum
from typing import Optional


class FolderType(Enum):
    AUTHOR = "author"
    PUBLISHER = "publisher"
    COLLECTION = "collection"
    VARIANT = "variant"
    NO_SERIES = "no_series"
    SKIP = "skip"
    UNKNOWN = "unknown"


# Folder names that are transparent pass-through containers (never a series/author)
_DEFAULT_SYSTEM_FOLDERS = frozenset({
    'fb2', 'epub', 'pdf', 'mobi', 'djvu', 'tmp', 'temp', 'covers',
    'cover', 'images', 'img', '_backup', 'backup', 'cache', '.cache',
})


class FolderClassifier:
    """Classifies a folder name into a semantic type using config-driven rules."""

    def __init__(self, settings):
        """
        Args:
            settings: SettingsManager instance
        """
        # Publisher prefixes (new config key; fallback to hardcoded defaults)
        raw_prefixes = settings.get('publisher_prefixes', None)
        if raw_prefixes and isinstance(raw_prefixes, list):
            self._publisher_prefixes = [p.lower() for p in raw_prefixes]
        else:
            self._publisher_prefixes = ['серия - «', 'серия «', 'серия - "', 'серия - ']

        # System/skip folders (new config key; fallback to defaults)
        raw_system = settings.get('system_folder_names', None)
        if raw_system and isinstance(raw_system, list):
            self._system_folders = frozenset(s.lower() for s in raw_system) | _DEFAULT_SYSTEM_FOLDERS
        else:
            self._system_folders = _DEFAULT_SYSTEM_FOLDERS

        # Blacklist words — genre/publisher markers (existing config key)
        self._blacklist_words = [w.lower() for w in settings.get_list('filename_blacklist') or []]

        # Collection keywords (existing config key)
        self._collection_keywords = [kw.lower() for kw in settings.get_list('collection_keywords') or []]

        # Variant/edition keywords (existing config key)
        self._variant_keywords = [kw.lower() for kw in settings.get_list('variant_folder_keywords') or []]

        # No-series folder names (existing config key) — exact match
        self._no_series_names = frozenset(
            name.lower() for name in settings.get_no_series_folder_names()
        )

        # Known first names for AUTHOR detection (existing config keys)
        male = settings.get_male_names() or []
        female = settings.get_female_names() or []
        self._known_names: frozenset = frozenset(
            name.lower().replace('ё', 'е') for name in (*male, *female)
        )

        # Pre-compile regex for stripping parenthetical content
        self._re_parens = re.compile(r'\s*\([^)]*\)')

    # ------------------------------------------------------------------
    def classify(self, folder_name: str) -> FolderType:
        """Return the FolderType for the given folder name.

        Args:
            folder_name: Bare folder name (not a full path).

        Returns:
            FolderType enum value.
        """
        name = folder_name.strip()
        if not name:
            return FolderType.UNKNOWN

        name_lower = name.lower()

        # 1. SKIP — technical folders
        if name_lower in self._system_folders:
            return FolderType.SKIP

        # 2. PUBLISHER — explicit prefixes take highest priority
        for prefix in self._publisher_prefixes:
            if name_lower.startswith(prefix):
                return FolderType.PUBLISHER

        # 3. PUBLISHER — blacklisted genre/publisher words (word-boundary match)
        for word in self._blacklist_words:
            # Use word boundary so "серия" doesn't match "Насерия"
            if re.search(r'(?:^|\W)' + re.escape(word) + r'(?:\W|$)', name_lower):
                return FolderType.PUBLISHER

        # 4. COLLECTION — collection keywords
        for kw in self._collection_keywords:
            if kw in name_lower:
                return FolderType.COLLECTION

        # 5. VARIANT — edition/variant keywords
        for kw in self._variant_keywords:
            if re.search(r'(?:^|\W)' + re.escape(kw) + r'(?:\W|$)', name_lower):
                return FolderType.VARIANT

        # 6. NO_SERIES — exact match
        if name_lower in self._no_series_names:
            return FolderType.NO_SERIES

        # 7. AUTHOR — name contains a known first name
        #    Strip parenthetical first: "Абрахам Дэниел (Джеймс С.А. Кори)" → "Абрахам Дэниел"
        name_stripped = self._re_parens.sub('', name).strip()
        for word in name_stripped.split():
            word_norm = word.strip('.,;:!?').lower().replace('ё', 'е')
            if word_norm in self._known_names:
                return FolderType.AUTHOR

        # 8. UNKNOWN — could be a series folder or anything else
        return FolderType.UNKNOWN

    # ------------------------------------------------------------------
    def classify_path_root(self, path_parts: tuple) -> FolderType:
        """Classify the top-level folder of a file path relative to work_dir.

        Args:
            path_parts: Tuple of path components (relative to work_dir, excluding filename).
                        E.g. ("Волков Тим", "Дуэлянт") for a file two levels deep.

        Returns:
            FolderType of the first (root) folder, or UNKNOWN if path_parts is empty.
        """
        if not path_parts:
            return FolderType.UNKNOWN
        return self.classify(path_parts[0])
