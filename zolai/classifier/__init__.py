"""Text classification for Zolai using Gemini ensemble + rule-based fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Zolai text classification topics
TOPICS = [
    "religion", "education", "news", "story",
    "grammar", "song", "proverb", "conversation",
]

# Keyword patterns for rule-based classification
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "religion": [
        "pasian", "topa", "biakna", "laisiangtho", "suahtakna",
        "nuntakna", "kumpipa", "vantung", "lehem", "kha",
        "kammi", "thupha", "hehpihna", "hehpih", "siangtho",
    ],
    "education": [
        "sinna", "gen", "thei", "zu", "ziak", "lehkhiat",
        "lai", "pilna", "sina", "kamkhan", "tun",
    ],
    "news": [
        "kammal", "siP", "tongsan", "phatsan", "hmangaih",
        "phahna", "suak", "hawm", "tepen", "napi",
    ],
    "story": [
        "tua ciangin", "a ci hi", "ciangin", "a om hi",
        "khin", "ta hi", "tua manin", "tua banah",
    ],
    "grammar": [
        "hiam", "kei", "lo", "in", "a", "ka", "na",
        "ding", "zo", "khin", "lai", "ta",
    ],
    "song": [
        "lasak", "lung", "lungdam", "singlamteh", "hawi",
        "lunglen", "kaih", "sa la", "pi la",
    ],
    "proverb": [
        "mau", "upna", "lunggulh", "linga",
        "pi nau", "uk tui", "mau ci",
    ],
    "conversation": [
        "lungdam", "kum", "dam", "hoih", "oih",
        "mai", "un", "ni", "aw", "ai",
    ],
}


class ZolaiClassifier:
    """Text classification for Zolai using Gemini ensemble."""

    def __init__(self, voter: Any = None):
        """Initialize classifier.

        Args:
            voter: EnsembleVoter instance for Gemini integration (optional).
        """
        self.voter = voter

    async def classify(self, text: str) -> dict:
        """Classify Zolai text topic using Gemini ensemble.

        Args:
            text: Zolai text to classify.

        Returns:
            {"topic": "religion", "confidence": 0.9}
        """
        if self.voter is None:
            return self.classify_rulebased(text)

        prompt = (
            "Classify this Zolai text into one of these topics: "
            f"{', '.join(TOPICS)}.\n"
            f"Text: {text}\n"
            'Output JSON: {"topic": "...", "confidence": 0.9}'
        )
        try:
            raw = await self.voter.vote(prompt)
            result = json.loads(raw) if isinstance(raw, str) else raw
            data = result.get("result", result)
            topic = data.get("topic", "conversation")
            confidence = float(data.get("confidence", 0.5))
            if topic not in TOPICS:
                topic = "conversation"
                confidence = 0.3
            return {"topic": topic, "confidence": min(confidence, 1.0)}
        except Exception as e:
            logger.warning("Gemini classification failed, falling back to rules: %s", e)
            return self.classify_rulebased(text)

    def classify_rulebased(self, text: str) -> dict:
        """Rule-based classification using keyword matching.

        Args:
            text: Zolai text to classify.

        Returns:
            {"topic": "religion", "confidence": 0.9}
        """
        text_lower = text.lower()
        scores: dict[str, float] = {}

        for topic, keywords in TOPIC_KEYWORDS.items():
            score = 0.0
            matched = 0
            for kw in keywords:
                if kw in text_lower:
                    score += 1.0
                    matched += 1
            # Normalize by keyword count
            if keywords:
                scores[topic] = score / len(keywords)
            else:
                scores[topic] = 0.0

        if not scores or max(scores.values()) == 0:
            return {"topic": "conversation", "confidence": 0.3}

        best_topic = max(scores, key=scores.get)
        best_score = scores[best_topic]

        # Scale confidence: 0.0-0.1 → 0.3-0.5, 0.1-0.3 → 0.5-0.7, 0.3+ → 0.7-0.95
        if best_score < 0.1:
            confidence = 0.3 + best_score * 2
        elif best_score < 0.3:
            confidence = 0.5 + best_score
        else:
            confidence = min(0.95, 0.7 + best_score * 0.5)

        return {"topic": best_topic, "confidence": round(confidence, 2)}

    async def classify_batch(self, texts: list[str]) -> list[dict]:
        """Classify multiple Zolai texts.

        Args:
            texts: List of Zolai texts.

        Returns:
            List of classification results.
        """
        results = []
        for text in texts:
            result = await self.classify(text)
            results.append(result)
        return results


# Module-level singleton
_classifier_instance: ZolaiClassifier | None = None


def get_classifier(voter: Any = None) -> ZolaiClassifier:
    """Get or create the singleton classifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = ZolaiClassifier(voter=voter)
    return _classifier_instance
