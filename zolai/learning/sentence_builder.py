"""
Zolai Sentence Builder — constructs new sentences from real Bible patterns ONLY.

NEVER guesses. Only uses attested patterns from the Bible.
"""
import random
from typing import Optional
from .bible_pattern_learner import get_bible_learner


class SentenceBuilder:
    """Build Zolai sentences using ONLY real Bible patterns."""

    def __init__(self):
        self.learner = get_bible_learner()

    def build_from_pattern(self, pattern_type: str = 'sov') -> dict:
        """Build a sentence using a real Bible pattern."""
        patterns = self.learner.get_patterns_for_type(pattern_type, limit=20)
        if not patterns:
            return {
                'success': False,
                'error': f'No {pattern_type} patterns found in Bible',
                'sentence': '',
            }
        pattern = random.choice(patterns)
        return {
            'success': True,
            'pattern_type': pattern_type,
            'sentence': pattern.example_zo,
            'english': pattern.example_en,
            'structure': pattern.structure,
            'components': pattern.components,
            'source': 'Bible (attested)',
        }

    def get_real_example(self, word: str, limit: int = 1) -> dict:
        """Get a real Bible sentence containing a word."""
        matching = []
        for verse in self.learner.verses:
            zo = verse.get('zo_tdb77') or ''
            if word.lower() in zo.lower():
                matching.append({
                    'ref': verse.get('ref', ''),
                    'zolai': zo,
                    'english': verse.get('en_kJV') or '',
                })
                if len(matching) >= limit:
                    break

        if not matching:
            return {
                'success': False,
                'error': f'No Bible sentences containing "{word}"',
                'sentence': '',
            }
        example = random.choice(matching)
        return {
            'success': True,
            'word': word,
            'sentence': example['zolai'],
            'english': example['english'],
            'reference': example['ref'],
            'source': 'Bible (attested)',
        }

    def build_with_word(self, word: str, pattern_type: str = 'sov') -> dict:
        """Build a sentence using a real pattern that contains the word."""
        real = self.get_real_example(word)
        if real['success']:
            return real
        pattern = self.build_from_pattern(pattern_type)
        return {
            'success': True,
            'word': word,
            'sentence': pattern['sentence'],
            'english': pattern['english'],
            'note': f'No real Bible sentence with "{word}" found. Using pattern example instead.',
            'source': 'Bible pattern (attested)',
        }

    def list_pattern_types(self) -> list[str]:
        """List available pattern types."""
        return list(self.learner.patterns.keys())

    def get_pattern_count(self) -> dict:
        """Get count of patterns by type."""
        return {ptype: len(plist) for ptype, plist in self.learner.patterns.items()}


# Singleton instance
_builder: Optional[SentenceBuilder] = None


def get_sentence_builder() -> SentenceBuilder:
    """Get or create sentence builder instance."""
    global _builder
    if _builder is None:
        _builder = SentenceBuilder()
    return _builder
