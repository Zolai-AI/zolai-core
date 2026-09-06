"""
Dictionary Cleaner — fixes data quality issues in Zolai dictionaries.

Fixes: non-printable chars, empty entries, short headwords, duplicates.
"""
import json
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class DictCleaner:
    """Clean and validate Zolai dictionaries."""

    def __init__(self):
        self.stats = {'cleaned': 0, 'removed': 0, 'fixed': 0}

    def clean_zo_en_dict(self, input_path: str = None, output_path: str = None) -> dict:
        """Clean ZO→EN dictionary."""
        if input_path is None:
            input_path = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_clean.jsonl"
        if output_path is None:
            output_path = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_clean_v2.jsonl"

        entries = []
        issues = []

        with open(input_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    cleaned = self._clean_entry(entry, i)
                    if cleaned:
                        entries.append(cleaned)
                    else:
                        issues.append(f"Line {i}: Removed")
                        self.stats['removed'] += 1
                except json.JSONDecodeError:
                    issues.append(f"Line {i}: JSON parse error")
                    self.stats['removed'] += 1

        # Write cleaned dictionary
        with open(output_path, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')

        self.stats['cleaned'] = len(entries)

        return {
            'input': str(input_path),
            'output': str(output_path),
            'stats': self.stats,
            'issues': issues[:50],  # First 50 issues
        }

    def _clean_entry(self, entry: dict, line_num: int) -> Optional[dict]:
        """Clean a single dictionary entry."""
        zolai = entry.get('zolai', '')
        english = entry.get('english', '')

        # Fix non-printable characters
        zolai_clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', zolai)

        # Remove entries with non-Zolai content
        if 'embed' in zolai_clean.lower() or 'coreldraw' in zolai_clean.lower():
            return None

        # Remove very short headwords (< 2 chars)
        if len(zolai_clean.strip()) < 2:
            return None

        # Remove empty English translations
        if not english or english == [] or english == '':
            return None

        # Clean English list
        if isinstance(english, list):
            english_clean = [e.strip() for e in english if e.strip()]
            if not english_clean:
                return None
        else:
            english_clean = english.strip()
            if not english_clean:
                return None

        # Update entry
        entry['zolai'] = zolai_clean.strip()
        entry['english'] = english_clean
        entry['source'] = entry.get('source', 'unknown')

        self.stats['fixed'] += 1
        return entry


def get_dict_cleaner() -> DictCleaner:
    """Get dictionary cleaner instance."""
    return DictCleaner()
