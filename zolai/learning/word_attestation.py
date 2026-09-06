"""
Word Attestation — verifies Zolai words exist in Bible or dictionary.

CRITICAL: The AI must NEVER use unattested words.
"""
import json
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class WordAttestation:
    """Verify if a Zolai word is attested in Bible or dictionary."""

    def __init__(self) -> None:
        self.bible_words: set[str] = set()
        self.dict_words: set[str] = set()
        self.corpus_words: set[str] = set()
        self._load_data()

    def _load_data(self) -> None:
        """Load all word sources."""
        # Bible words
        bible_path = DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
        if bible_path.exists():
            with open(bible_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zo = entry.get("zo_tdb77") or ""
                        words = re.findall(r"\b[a-zA-Z\u0100-\u024F'-]+\b", zo.lower())
                        self.bible_words.update(w for w in words if len(w) >= 2)
                    except (json.JSONDecodeError, AttributeError):
                        continue

        # Dictionary words
        dict_path = DATA_DIR / "dictionary" / "processed" / "dict_zo_en_clean.jsonl"
        if dict_path.exists():
            with open(dict_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zolai = str(entry.get("zolai", "")).lower().strip()
                        if zolai and len(zolai) >= 2:
                            self.dict_words.add(zolai)
                    except (json.JSONDecodeError, AttributeError):
                        continue

        # Parallel corpus words
        parallel_path = DATA_DIR / "parallel" / "zo_en_pairs_combined_v1.jsonl"
        if parallel_path.exists():
            with open(parallel_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zo = entry.get("zolai", "")
                        words = re.findall(r"\b[a-zA-Z\u0100-\u024F'-]+\b", zo.lower())
                        self.corpus_words.update(w for w in words if len(w) >= 2)
                    except (json.JSONDecodeError, AttributeError):
                        continue

    def attest_word(self, word: str) -> dict:
        """Check if a word is attested in any source."""
        word_lower = word.lower().strip()

        in_bible = word_lower in self.bible_words
        in_dict = word_lower in self.dict_words
        in_corpus = word_lower in self.corpus_words

        sources = sum([in_bible, in_dict, in_corpus])
        if sources >= 2:
            confidence = "VERIFIED"
        elif sources == 1:
            confidence = "ATTESTED"
        else:
            confidence = "UNATTESTED"

        return {
            "word": word,
            "confidence": confidence,
            "in_bible": in_bible,
            "in_dict": in_dict,
            "in_corpus": in_corpus,
            "source_count": sources,
        }

    def attest_sentence(self, sentence: str) -> dict:
        """Check if all words in a sentence are attested."""
        words = re.findall(r"\b[a-zA-Z\u0100-\u024F'-]+\b", sentence.lower())

        results: list[dict] = []
        unattested: list[str] = []

        for word in words:
            if len(word) < 2:
                continue
            result = self.attest_word(word)
            results.append(result)
            if result["confidence"] == "UNATTESTED":
                unattested.append(word)

        verified = sum(1 for r in results if r["confidence"] == "VERIFIED")
        attested = sum(1 for r in results if r["confidence"] == "ATTESTED")
        total = len(results)

        if total == 0:
            score = 0.0
        else:
            score = (verified * 1.0 + attested * 0.5) / total

        if score >= 0.8:
            overall = "PASS"
        elif score >= 0.5:
            overall = "PARTIAL"
        else:
            overall = "FAIL"

        return {
            "sentence": sentence,
            "overall": overall,
            "score": round(score, 3),
            "total_words": total,
            "verified": verified,
            "attested": attested,
            "unattested": unattested,
            "word_results": results,
        }

    def get_suggestion(self, word: str) -> Optional[str]:
        """Suggest correct word for unattested word."""
        word_lower = word.lower().strip()

        # Find similar words in Bible
        similar: list[str] = []
        for bw in self.bible_words:
            if len(bw) >= 3 and (word_lower in bw or bw in word_lower):
                similar.append(bw)
            elif self._levenshtein(word_lower, bw) <= 2:
                similar.append(bw)

        if similar:
            return similar[0]
        return None

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        """Calculate Levenshtein distance."""
        if len(s1) < len(s2):
            return WordAttestation._levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)

        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    def get_stats(self) -> dict:
        """Get attestation statistics."""
        return {
            "bible_words": len(self.bible_words),
            "dict_words": len(self.dict_words),
            "corpus_words": len(self.corpus_words),
        }


# Singleton instance
_attestation: Optional[WordAttestation] = None


def get_word_attestation() -> WordAttestation:
    """Get word attestation instance."""
    global _attestation  # noqa: PLW0603
    if _attestation is None:
        _attestation = WordAttestation()
    return _attestation
