"""Rule-based POS tagger for Tedim Zolai using dictionary + grammar patterns.

POS tag set based on Sino-Tibetan typology:
    NOUN, VERB, ADJ, ADV, PRON, DET, POST, CONJ, PART, NUM, INTJ, PUNCT, X

Subtags:
    NOUN: N.PROPER, N.COMMON, N.COMPOUND
    VERB: V.INTRANS, V.TRANS, V.DITRANS, V.AUX, V.COP
    ADJ: ADJ.QUAL, ADJ.QUANT
    PART: PART.NEG, PART.QUESTION, PART.FOCUS, PART.TOPIC, PART.ERG
    POST: POST.LOC, POST.DAT, POST.GEN, POST.INS

Usage:
    from zolai.pos_tagger import ZolaiPOSTagger
    tagger = ZolaiPOSTagger()
    tagged = tagger.tag("Pasian in vantung leh leitung a piangsak hi.")
    # [('Pasian', 'N.PROPER'), ('in', 'PART.ERG'), ...]
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

# ── Pronouns (closed class) ──────────────────────────────────────────────────
_PRONOUNS: dict[str, str] = {
    "ka": "PRON",
    "na": "PRON",
    "a": "PRON",
    "i": "PRON",
    "ki": "PRON",
    "amah": "PRON",
    "u": "PRON",
    "nau": "PRON",
    "uh": "PRON",
    "hihte": "PRON",
    "amaute": "PRON",
    "huate": "PRON",
    "naute": "PRON",
}

# ── Particles (closed class) ─────────────────────────────────────────────────
_PARTICLES: dict[str, str] = {
    # Negation
    "kei": "PART.NEG",
    "lo": "PART.NEG",
    # Question
    "hiam": "PART.QUESTION",
    "diam": "PART.QUESTION",
    # Ergative
    "in": "PART.ERG",
    # Focus
    "pen": "PART.FOCUS",
    # Topic
    "bang": "PART.TOPIC",
    # Agreement markers
    "ding": "PART",
    # Discourse
    "ci": "PART",
    "leh": "PART",
    "tua": "PART",
    "aw": "PART",
    "ni": "PART",
    "un": "PART",
    "hen": "PART",
    "lah": "PART",
    "te": "PART",
    "teh": "PART",
    "hi": "PART",
    "he": "PART",
}

# ── Postpositions ─────────────────────────────────────────────────────────────
_POSTPOSITIONS: dict[str, str] = {
    "tawh": "POST",
    "ah": "POST",
    "sungah": "POST.LOC",
    "tungh": "POST.LOC",
    "lam": "POST.LOC",
    "pih": "POST",
    "tua": "POST",
}

# ── Conjunctions ──────────────────────────────────────────────────────────────
_CONJUNCTIONS: dict[str, str] = {
    "leh": "CONJ",
    "banah": "CONJ",
    "tua": "CONJ",
    "te": "CONJ",
    "huna": "CONJ",
    "ciangin": "CONJ",
    "panin": "CONJ",
    "kumin": "CONJ",
}

# ── Determiners ───────────────────────────────────────────────────────────────
_DETERMINERS: set[str] = {
    "khat", "pathum", "ahte", "hong", "ahte",
}

# ── Numerals ──────────────────────────────────────────────────────────────────
_NUMERALS: dict[str, str] = {
    "khat": "NUM",
    "hnih": "NUM",
    "thum": "NUM",
    "li": "NUM",
    "nga": "NUM",
    "tuk": "NUM",
    "sagi": "NUM",
    "gait": "NUM",
    "kuar": "NUM",
    "kuarkua": "NUM",
    "sang": "NUM",
    "sangkua": "NUM",
}

# ── Verb suffixes (for heuristic detection) ───────────────────────────────────
_VERB_SUFFIXES: tuple[str, ...] = (
    "tak", "sak", "ah", "hen", "ding", "lo",
    "na", "hna", "tawh", "zo", "lai", "phei",
    "hia", "kia", "lut", "khia", "mang",
)

# ── Noun suffixes (for heuristic detection) ───────────────────────────────────
_NOUN_SUFFIXES: tuple[str, ...] = (
    "na", "hna", "ma", "mi", "te", "in", "ah",
    "tang", "tung", "lan", "lam", "thak",
)

# ── Proper noun indicators (Bible attestation) ───────────────────────────────
_BIBLE_PROPER: set[str] = {
    "pasian", "topa", "kumpipa", "adam", "eva",
    "israel", "abraham", "mozes", "dawid", "solomon",
    "jesus", "yakub", "yosef", "maria", "henry",
    "tedim", "zomi", "khanggui", "khangthak", "kaulun",
    "selena", "lantern", "laisiangtho", "siangtho",
}

# ── POS tag mapping from dictionary ───────────────────────────────────────────
_DICT_POS_MAP: dict[str, str] = {
    "n": "NOUN",
    "v": "VERB",
    "adj": "ADJ",
    "adv": "ADV",
    "pron": "PRON",
    "det": "DET",
    "post": "POST",
    "conj": "CONJ",
    "part": "PART",
    "num": "NUM",
    "intj": "INTJ",
    "prep": "POST",
}


class ZolaiPOSTagger:
    """Rule-based POS tagger for Zolai using dictionary + patterns.

    Lazy-loads data on first tag() call. Uses:
    - Dictionary POS labels (93K entries)
    - Pronoun/particle/postposition closed classes
    - Suffix heuristics for unknown words
    - Capitalization + Bible attestation for proper nouns
    """

    def __init__(self) -> None:
        self._dict_pos: dict[str, str] = {}
        self._bible_words: set[str] = set()
        self._vocab_freq: dict[str, int] = {}
        self._loaded = False

    # ── Lazy loading ──────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        """Load data on first use (lazy loading)."""
        if self._loaded:
            return
        self._loaded = True
        self._load_dict()
        self._load_bible()
        self._load_vocab()

    def _load_dict(self) -> None:
        """Load POS labels from dictionary master file."""
        dict_path = (
            _DATA_DIR / "dictionary" / "processed" / "dict_zo_en_master_v1.jsonl"
        )
        if not dict_path.exists():
            return
        count = 0
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = __import__("json").loads(line)
                        word = str(entry.get("zolai", "")).lower().strip()
                        pos = str(entry.get("pos", "")).lower().strip()
                        if word and pos and len(word) >= 2:
                            self._dict_pos[word] = _DICT_POS_MAP.get(pos, pos.upper())
                            count += 1
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            return
        log.debug("POS tagger: loaded %d dict POS entries", count)

    def _load_bible(self) -> None:
        """Load Bible words for proper noun detection."""
        bible_path = _DATA_DIR / "bible" / "parallel_corpus_v1.jsonl"
        if not bible_path.exists():
            return
        try:
            import json as _json
            with open(bible_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = _json.loads(line)
                        zo = entry.get("zo_tdb77") or ""
                        words = re.findall(r"\b[a-zA-Z\u0100-\u024F'-]+\b", zo.lower())
                        self._bible_words.update(w for w in words if len(w) >= 2)
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            return
        log.debug("POS tagger: loaded %d Bible words", len(self._bible_words))

    def _load_vocab(self) -> None:
        """Load vocabulary frequency from vocab table."""
        vocab_path = _DATA_DIR / "bible" / "language_learning" / "vocab_index_full.jsonl"
        if not vocab_path.exists():
            return
        try:
            import json as _json
            with open(vocab_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = _json.loads(line)
                        word = entry.get("word", "").lower()
                        freq = entry.get("frequency", 0)
                        if word:
                            self._vocab_freq[word] = freq
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            return

    # ── Core tagging ──────────────────────────────────────────────────────
    def tag(self, sentence: str) -> list[tuple[str, str]]:
        """Tag a sentence into (word, POS) pairs.

        Args:
            sentence: Zolai text, e.g. "Pasian in vantung leh leitung a piangsak hi."

        Returns:
            List of (word, POS tag) tuples, e.g.
            [('Pasian', 'N.PROPER'), ('in', 'PART.ERG'), ...]
        """
        words = sentence.split()
        return self.tag_words(words)

    def tag_words(self, words: list[str]) -> list[tuple[str, str]]:
        """Tag a list of words with POS labels.

        Args:
            words: List of Zolai words.

        Returns:
            List of (word, POS tag) tuples.
        """
        self._ensure_loaded()
        return [self._tag_single(w) for w in words]

    def _tag_single(self, word: str) -> tuple[str, str]:
        """Tag a single word with POS."""
        lower = word.lower()
        clean = re.sub(r"[^\w]", "", lower)

        # 1. Punctuation
        if re.match(r"^[.!?,;:]+$", word):
            return (word, "PUNCT")

        # 2. Pronouns (closed class)
        if clean in _PRONOUNS:
            return (word, _PRONOUNS[clean])

        # 3. Particles (closed class — highest priority for function words)
        if clean in _PARTICLES:
            return (word, _PARTICLES[clean])

        # 4. Conjunctions (check before dict lookup since "leh" etc. are common)
        if clean in _CONJUNCTIONS:
            return (word, _CONJUNCTIONS[clean])

        # 5. Postpositions
        if clean in _POSTPOSITIONS:
            return (word, _POSTPOSITIONS[clean])

        # 6. Numerals
        if clean in _NUMERALS:
            return (word, _NUMERALS[clean])

        # 7. Determiners
        if clean in _DETERMINERS:
            return (word, "DET")

        # 8. Dictionary POS lookup
        if clean in self._dict_pos:
            return (word, self._dict_pos[clean])

        # 9. Proper noun detection (capitalized + Bible attested)
        if self._is_proper_noun(word, clean):
            return (word, "N.PROPER")

        # 10. Suffix heuristics for unknown words
        pos = self._guess_by_suffix(clean)
        if pos:
            return (word, pos)

        # 11. Default: common noun
        return (word, "NOUN")

    def _is_proper_noun(self, original: str, lower: str) -> bool:
        """Check if word is a proper noun.

        Rules:
        - Capitalized in original form
        - Attested in Bible corpus OR in proper noun set
        """
        if original[0].isupper() and original[1:].islower():
            # Capitalized — likely proper noun
            if lower in _BIBLE_PROPER or lower in self._bible_words:
                return True
            # Even without Bible attestation, capitalized Zolai words
            # are usually proper nouns (names, places)
            if lower not in _PRONOUNS and lower not in _PARTICLES:
                return True
        return False

    def _guess_by_suffix(self, word: str) -> Optional[str]:
        """Guess POS from word suffix (Zolai agglutinative morphology)."""
        if len(word) < 4:
            return None

        # Verb suffixes: -tak (completive), -sak (past), -ah (progressive),
        # -hen (experiential), -ding (future), -zo (completive), -lai (progressive)
        for suffix in _VERB_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix) + 2:
                return "VERB"

        # Noun suffixes: -na (nominalizer), -hna (abstract noun), -ma/-mi (agent)
        # -tang (place), -tung (place), -lan (instrument), -lam (manner)
        for suffix in _NOUN_SUFFIXES:
            if word.endswith(suffix) and len(word) > len(suffix) + 2:
                return "NOUN"

        return None

    def tag_with_confidence(self, sentence: str) -> list[tuple[str, str, float]]:
        """Tag with confidence scores (0.0–1.0).

        Returns:
            List of (word, POS tag, confidence) tuples.
        """
        tagged = self.tag(sentence)
        return [
            (word, pos, self._confidence(word, pos))
            for word, pos in tagged
        ]

    def _confidence(self, word: str, pos: str) -> float:
        """Estimate confidence for a POS tag."""
        lower = word.lower()

        # High confidence for closed classes
        if lower in _PRONOUNS or lower in _PARTICLES:
            return 1.0
        if lower in _CONJUNCTIONS or lower in _POSTPOSITIONS:
            return 0.95
        if lower in _NUMERALS:
            return 0.95

        # High confidence for dictionary match
        if lower in self._dict_pos:
            return 0.90

        # Medium confidence for proper nouns
        if pos == "N.PROPER":
            return 0.80

        # Low confidence for suffix guesses
        if pos == "VERB" or pos == "NOUN":
            if lower in self._vocab_freq:
                return 0.70
            return 0.50

        return 0.60

    def get_stats(self) -> dict[str, int]:
        """Return tagger statistics."""
        self._ensure_loaded()
        return {
            "dict_pos_entries": len(self._dict_pos),
            "bible_words": len(self._bible_words),
            "vocab_entries": len(self._vocab_freq),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_tagger: Optional[ZolaiPOSTagger] = None


def get_pos_tagger() -> ZolaiPOSTagger:
    """Get or create the singleton POS tagger."""
    global _tagger  # noqa: PLW0603
    if _tagger is None:
        _tagger = ZolaiPOSTagger()
    return _tagger
