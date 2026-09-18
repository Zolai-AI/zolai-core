"""
Sentence Validator — checks if a Zolai sentence is real/attested.
Uses Bible + corpus as ground truth. Lazy loading.
Set ZOLAI_LOAD_CORPUS=1 to include  corpus (~208MB, slow load).
"""
import os
import re
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


class SentenceValidator:
    """Validate if a Zolai sentence is real/attested."""

    def __init__(self):
        self.bible_sentences: set[str] = set()
        self.corpus_sentences: set[str] = set()
        self.bible_words: set[str] = set()
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_bible()
        self._load_parallel()
        if os.environ.get("ZOLAI_LOAD_CORPUS", "") == "1":
            self._load_()

    def _load_bible(self):
        from ..data.repositories import get_repositories

        repos = get_repositories()
        for r in repos["bible"].all_records(["zo_tdb77", "zo_tedim2010"]):
            zo = (r.get("zo_tdb77") or r.get("zo_tedim2010") or "").strip()
            if zo:
                normalized = self._normalize(zo)
                self.bible_sentences.add(normalized)
                words = re.findall(r"\b[a-zA-Z'-]+\b", zo.lower())
                self.bible_words.update(words)

    def _load_parallel(self):
        from ..data.repositories import get_repositories

        repos = get_repositories()
        for r in repos["translation"].all_records(["target"]):
            zo = (r.get("target") or "").strip()
            if zo:
                normalized = self._normalize(zo)
                self.corpus_sentences.add(normalized)

    def _load_(self):
        """Load  corpus for modern Zolai validation."""
        corpus_dir = DATA_DIR / "online" / "zolai-web-corpus"
        if not corpus_dir.exists():
            return
        for txt_file in corpus_dir.glob("zomi_clean_p*.txt"):
            try:
                with open(txt_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            normalized = self._normalize(line)
                            self.corpus_sentences.add(normalized)
            except Exception:
                continue

    def _normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[.,;:!?]", "", text)
        return text

    def validate(self, sentence: str) -> dict:
        self._ensure_loaded()
        normalized = self._normalize(sentence)

        in_bible = normalized in self.bible_sentences
        in_corpus = normalized in self.corpus_sentences

        # Query may be a verse fragment; check if normalized text appears inside attested sentences
        partial_bible = any(
            normalized in b for b in self.bible_sentences
            if len(normalized) > 10 and len(b) > len(normalized)
        )
        partial_corpus = any(
            normalized in c for c in self.corpus_sentences
            if len(normalized) > 10 and len(c) > len(normalized)
        )

        words = re.findall(r"\b[a-zA-Z'-]+\b", sentence.lower())
        bible_words_found = [w for w in words if w in self.bible_words]
        word_score = len(bible_words_found) / max(len(words), 1)

        if in_bible or in_corpus:
            confidence = "VERIFIED"
        elif partial_bible or partial_corpus:
            confidence = "PARTIAL"
        elif word_score > 0.7:
            confidence = "HIGH"
        elif word_score > 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return {
            "sentence": sentence,
            "confidence": confidence,
            "in_bible": in_bible,
            "in_corpus": in_corpus,
            "partial_match": partial_bible or partial_corpus,
            "word_score": word_score,
            "bible_words_found": bible_words_found,
            "total_words": len(words),
        }

    def get_stats(self) -> dict:
        self._ensure_loaded()
        return {
            "bible_sentences": len(self.bible_sentences),
            "corpus_sentences": len(self.corpus_sentences),
            "bible_words": len(self.bible_words),
        }


_validator: Optional[SentenceValidator] = None


def get_sentence_validator() -> SentenceValidator:
    global _validator  # noqa: PLW0603
    if _validator is None:
        _validator = SentenceValidator()
    return _validator
