"""
Zolai AI Accuracy Scorer — cross-reference multiple data sources.

Scores words/phrases by confidence based on dictionary + Bible + grammar,
all read from the canonical DB (no runtime JSONL).
"""

from ..config import config
from ..data.database import get_manager


class AccuracyScorer:
    """Cross-reference accuracy scoring for Zolai words/phrases."""

    def __init__(self):
        self._db = get_manager(f"sqlite:///{config.paths.zolai_db}")

    def score_word(self, word: str) -> dict:
        """Score a single word's accuracy/confidence."""
        sources = []
        wl = word.lower()

        # Check dictionary
        dict_matches = self._db.lookup_word(wl)
        if dict_matches:
            sources.append(("dictionary", len(dict_matches)))

        # Check Bible
        bible_matches = [
            v for v in self._db.search_bible(wl)
        ]
        if bible_matches:
            sources.append(("bible", len(bible_matches)))

        # Check grammar patterns
        grammar_matches = self._db.get_grammar(wl)
        if grammar_matches:
            sources.append(("grammar", len(grammar_matches)))

        # Calculate confidence
        source_count = len(sources)
        if source_count >= 3:
            confidence = "HIGH"
        elif source_count == 2:
            confidence = "MEDIUM"
        elif source_count == 1:
            confidence = "LOW"
        else:
            confidence = "UNCERTAIN"

        return {
            "word": word,
            "confidence": confidence,
            "sources": sources,
            "source_count": source_count,
        }

    def score_phrase(self, words: list[str]) -> dict:
        """Score a phrase/sequence of words."""
        scores = [self.score_word(w) for w in words]

        # Overall confidence is the minimum of individual confidences
        confidence_levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNCERTAIN": 0}
        min_confidence = min(confidence_levels.get(s["confidence"], 0) for s in scores)

        reverse_levels = {3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "UNCERTAIN"}

        return {
            "words": words,
            "overall_confidence": reverse_levels[min_confidence],
            "word_scores": scores,
            "total_sources": sum(s["source_count"] for s in scores),
        }


def get_accuracy_scorer():
    """Get or create accuracy scorer instance."""
    return AccuracyScorer()
