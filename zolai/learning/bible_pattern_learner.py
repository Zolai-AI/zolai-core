"""
Bible Pattern Learner — extracts real Zolai sentence patterns from Bible.

This is the GROUND TRUTH. The AI must NEVER guess — only use attested patterns.
"""
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

BIBLE_PATH = Path(__file__).parent.parent.parent.parent / "data" / "bible" / "parallel_corpus_v1.jsonl"


@dataclass
class SentencePattern:
    """A real Zolai sentence pattern from the Bible."""
    pattern_type: str
    structure: str
    example_zo: str
    example_en: str
    frequency: int = 1
    components: dict = field(default_factory=dict)


class BiblePatternLearner:
    """Learn Zolai sentence patterns from the Bible."""

    def __init__(self):
        self.verses: list[dict] = []
        self.patterns: dict[str, list[SentencePattern]] = defaultdict(list)
        self.word_freq: Counter = Counter()
        self.phrase_patterns: Counter = Counter()
        self._load_bible()

    def _load_bible(self):
        """Load Bible parallel corpus."""
        if not BIBLE_PATH.exists():
            print(f"Bible not found at {BIBLE_PATH}")
            return
        with open(BIBLE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    # Skip verses with None Zolai text
                    zo = entry.get('zo_tdb77') or ''
                    if zo.strip():
                        self.verses.append(entry)
        print(f"Loaded {len(self.verses)} Bible verses (filtered None)")

    def extract_patterns(self):
        """Extract all pattern types from Bible."""
        for verse in self.verses:
            zo = verse.get('zo_tdb77') or ''
            en = verse.get('en_kJV') or ''
            if not zo.strip():
                continue

            pattern_type = self._classify_pattern(zo)
            structure = self._extract_structure(zo)
            components = self._extract_components(zo)

            words = re.findall(r'\b[a-zA-Z\'-]+\b', zo.lower())
            self.word_freq.update(words)

            pattern = SentencePattern(
                pattern_type=pattern_type,
                structure=structure,
                example_zo=zo,
                example_en=en,
                components=components,
            )
            self.patterns[pattern_type].append(pattern)

        for ptype, plist in self.patterns.items():
            self.phrase_patterns[ptype] = len(plist)

    def _classify_pattern(self, text: str) -> str:
        """Classify sentence pattern type."""
        t = text.lower()
        if 'hiam' in t:
            return 'question'
        if re.search(r'\b(lo|kei)\b', t):
            return 'negation'
        if 'ci hi' in text or 'ci-in' in text:
            return 'quotative'
        if re.search(r'-sak\b', text):
            return 'past_completed'
        if 'ding' in t:
            return 'future'
        if re.search(r'\bin\b', text):
            return 'ergative'
        return 'sov'

    def _extract_structure(self, text: str) -> str:
        """Extract abstract sentence structure."""
        if 'ci hi' in text:
            return "Subject in, 'Speech,' ci hi"
        elif 'in' in text and 'hi' in text:
            return "Subject in Object Verb hi"
        elif 'hiam' in text:
            return "Subject Verb hiam?"
        else:
            return "Subject Object Verb"

    def _extract_components(self, text: str) -> dict:
        """Extract grammatical components from sentence."""
        components = {}
        erg_match = re.search(r'(\w+)\s+in\b', text)
        if erg_match:
            components['subject'] = erg_match.group(1)
        verb_match = re.search(r'(\w+)\s+hi\b', text)
        if verb_match:
            components['verb'] = verb_match.group(1)
        speech_match = re.search(r'"([^"]+)"', text)
        if speech_match:
            components['speech'] = speech_match.group(1)
        return components

    def get_patterns_for_type(self, pattern_type: str, limit: int = 10) -> list[SentencePattern]:
        """Get examples of a specific pattern type."""
        return self.patterns.get(pattern_type, [])[:limit]

    def get_sentence_examples(self, count: int = 5) -> list[dict]:
        """Get random real Zolai sentences from Bible."""
        import random
        if not self.verses:
            return []
        samples = random.sample(self.verses, min(count, len(self.verses)))
        return [
            {
                'ref': v.get('ref', ''),
                'zolai': v.get('zo_tdb77') or '',
                'english': v.get('en_kJV') or '',
                'pattern': self._classify_pattern(v.get('zo_tdb77') or ''),
            }
            for v in samples
        ]

    def get_pattern_stats(self) -> dict:
        """Get statistics about extracted patterns."""
        return {
            'total_verses': len(self.verses),
            'pattern_counts': dict(self.phrase_patterns),
            'top_words': self.word_freq.most_common(50),
        }

    def build_context_for_word(self, word: str, limit: int = 3, max_tokens: int = 200) -> str:
        """Build context showing real Bible sentences containing a word.
        
        Args:
            word: Word to search for
            limit: Max sentences to return (default 3 to save tokens)
            max_tokens: Max tokens for entire output (~4 chars per token)
        """
        matching = []
        char_budget = max_tokens * 4
        used_chars = 0

        for verse in self.verses:
            zo = verse.get('zo_tdb77') or ''
            if word.lower() in zo.lower():
                entry_chars = len(zo) + len(verse.get('en_kJV') or '') + 50
                if used_chars + entry_chars > char_budget:
                    break
                matching.append({
                    'ref': verse.get('ref', ''),
                    'zolai': zo,
                    'english': verse.get('en_kJV') or '',
                })
                used_chars += entry_chars
                if len(matching) >= limit:
                    break

        if not matching:
            return f"No Bible sentences found containing '{word}'"

        lines = [f"## Bible: '{word}'\n"]
        for m in matching:
            lines.append(f"**{m['ref']}**: {m['zolai']}")
            lines.append(f"  EN: {m['english']}\n")

        return "\n".join(lines)

    def build_pattern_context(self, pattern_type: str = 'sov', limit: int = 3, max_tokens: int = 200) -> str:
        """Build context showing real pattern examples.
        
        Args:
            pattern_type: Pattern type to show
            limit: Max examples (default 3 to save tokens)
            max_tokens: Max tokens for entire output
        """
        patterns = self.get_patterns_for_type(pattern_type, limit)

        if not patterns:
            return f"No patterns of type '{pattern_type}' found"

        char_budget = max_tokens * 4
        lines = [f"## {pattern_type.upper()} pattern\n"]
        used_chars = len(lines[0])

        for p in patterns:
            entry = f"**{p.example_zo}**\n  EN: {p.example_en}\n  Structure: {p.structure}\n\n"
            if used_chars + len(entry) > char_budget:
                break
            lines.append(entry)
            used_chars += len(entry)

        return "".join(lines)


# Singleton instance
_learner: Optional[BiblePatternLearner] = None


def get_bible_learner() -> BiblePatternLearner:
    """Get or create Bible pattern learner instance."""
    global _learner
    if _learner is None:
        _learner = BiblePatternLearner()
        _learner.extract_patterns()
    return _learner
