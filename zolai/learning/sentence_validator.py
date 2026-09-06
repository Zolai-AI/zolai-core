"""
Sentence Validator — checks if a Zolai sentence is real/attested.

Uses Bible + corpus as ground truth. Never validates guessed sentences.
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class SentenceValidator:
    """Validate if a Zolai sentence is real/attested."""

    def __init__(self):
        self.bible_sentences: set[str] = set()
        self.corpus_sentences: set[str] = set()
        self.bible_words: set[str] = set()
        self._load_data()

    def _load_data(self):
        """Load Bible and corpus for validation."""
        # Load Bible sentences
        bible_path = DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
        if bible_path.exists():
            with open(bible_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zo = entry.get('zo_tdb77') or ''
                        if zo.strip():
                            # Normalize for comparison
                            normalized = self._normalize(zo)
                            self.bible_sentences.add(normalized)
                            # Extract words
                            words = re.findall(r'\b[a-zA-Z\'-]+\b', zo.lower())
                            self.bible_words.update(words)
                    except Exception:
                        continue

        # Load parallel corpus
        parallel_path = DATA_DIR / "parallel" / "zo_en_pairs_combined_v1.jsonl"
        if parallel_path.exists():
            with open(parallel_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        zo = entry.get('zolai', '')
                        if zo.strip():
                            normalized = self._normalize(zo)
                            self.corpus_sentences.add(normalized)
                    except Exception:
                        continue

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase, remove extra spaces, normalize punctuation
        text = text.lower().strip()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[.,;:!?]', '', text)
        return text

    def validate(self, sentence: str) -> dict:
        """Validate if a sentence is real/attested."""
        normalized = self._normalize(sentence)

        # Check Bible
        in_bible = normalized in self.bible_sentences

        # Check corpus
        in_corpus = normalized in self.corpus_sentences

        # Check partial match (sentence contains Bible sentence)
        partial_bible = any(b in normalized for b in self.bible_sentences if len(b) > 10)
        partial_corpus = any(c in normalized for c in self.corpus_sentences if len(c) > 10)

        # Check word authenticity
        words = re.findall(r'\b[a-zA-Z\'-]+\b', sentence.lower())
        bible_words_found = [w for w in words if w in self.bible_words]
        word_score = len(bible_words_found) / max(len(words), 1)

        # Calculate confidence
        if in_bible or in_corpus:
            confidence = 'VERIFIED'
        elif partial_bible or partial_corpus:
            confidence = 'PARTIAL'
        elif word_score > 0.7:
            confidence = 'HIGH'
        elif word_score > 0.4:
            confidence = 'MEDIUM'
        else:
            confidence = 'LOW'

        return {
            'sentence': sentence,
            'confidence': confidence,
            'in_bible': in_bible,
            'in_corpus': in_corpus,
            'partial_match': partial_bible or partial_corpus,
            'word_score': word_score,
            'bible_words_found': bible_words_found,
            'total_words': len(words),
        }

    def get_stats(self) -> dict:
        """Get validation statistics."""
        return {
            'bible_sentences': len(self.bible_sentences),
            'corpus_sentences': len(self.corpus_sentences),
            'bible_words': len(self.bible_words),
        }


def get_sentence_validator() -> SentenceValidator:
    """Get sentence validator instance."""
    return SentenceValidator()
