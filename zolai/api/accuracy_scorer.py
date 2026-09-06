"""
Zolai AI Accuracy Scorer — cross-reference multiple data sources.

Scores words/phrases by confidence based on dictionary + Bible + wiki + grammar.
"""
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class AccuracyScorer:
    """Cross-reference accuracy scoring for Zolai words/phrases."""

    def __init__(self):
        self.dict_zo_en = self._load_jsonl(
            DATA_DIR / "dictionary" / "dict_zo_en_clean.jsonl"
        )
        self.bible = self._load_jsonl(
            DATA_DIR / "bible" / "parallel_corpus.jsonl"
        )
        grammar_path = DATA_DIR / "wiki" / "grammar_patterns.json"
        self.grammar = (
            self._load_json(grammar_path) if grammar_path.exists() else {}
        )

    def _load_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        data = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))
        return data

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def score_word(self, word: str) -> dict:
        """Score a single word's accuracy/confidence."""
        sources = []

        wl = word.lower()

        # Check dictionary
        dict_matches = [
            e for e in self.dict_zo_en
            if wl in str(e.get('zolai', '')).lower()
            or wl in str(e.get('english', '')).lower()
        ]
        if dict_matches:
            sources.append(('dictionary', len(dict_matches)))

        # Check Bible
        bible_matches = [
            v for v in self.bible
            if wl in str(v.get('zo', '')).lower()
            or wl in str(v.get('english', '')).lower()
        ]
        if bible_matches:
            sources.append(('bible', len(bible_matches)))

        # Check grammar patterns
        grammar_matches = [
            p for p in self.grammar.get('patterns', [])
            if wl in str(p.get('example', '')).lower()
        ]
        if grammar_matches:
            sources.append(('grammar', len(grammar_matches)))

        # Calculate confidence
        source_count = len(sources)
        if source_count >= 3:
            confidence = 'HIGH'
        elif source_count == 2:
            confidence = 'MEDIUM'
        elif source_count == 1:
            confidence = 'LOW'
        else:
            confidence = 'UNCERTAIN'

        return {
            'word': word,
            'confidence': confidence,
            'sources': sources,
            'source_count': source_count,
        }

    def score_phrase(self, words: list[str]) -> dict:
        """Score a phrase/sequence of words."""
        scores = [self.score_word(w) for w in words]

        # Overall confidence is the minimum of individual confidences
        confidence_levels = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1, 'UNCERTAIN': 0}
        min_confidence = min(confidence_levels.get(s['confidence'], 0) for s in scores)

        reverse_levels = {3: 'HIGH', 2: 'MEDIUM', 1: 'LOW', 0: 'UNCERTAIN'}

        return {
            'words': words,
            'overall_confidence': reverse_levels[min_confidence],
            'word_scores': scores,
            'total_sources': sum(s['source_count'] for s in scores),
        }


def get_accuracy_scorer():
    """Get or create accuracy scorer instance."""
    return AccuracyScorer()
