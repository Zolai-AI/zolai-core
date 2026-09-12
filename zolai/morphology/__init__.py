"""Morphological analyzer for Tedim Zolai.

Zolai is agglutinative with:
- Prefixes: ka- (1SG), na- (2SG), a- (3SG), i- (1PL.EXCL), ki- (1PL.INCL)
- Suffixes: -na (nominalizer), -tak (completive), -sak (past), -ah (progressive),
  -hen (experiential), -ding (future), -lo (negative)
- Reduplication: partial (CVCV→CVCV), full (CVCV→CVCVCVCV)
- Compounds: head+modifier (vantung = van+tung)

Usage:
    from zolai.morphology import ZolaiMorphology
    morph = ZolaiMorphology()
    result = morph.analyze("nuntakna")
    # {'stem': 'tak', 'prefix': '', 'suffix': 'na', 'root': 'nun', 'POS': 'NOUN'}
    base = morph.lemmatize("nuntakna")
    # 'nun'
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

# ── Prefixes (person agreement / valency) ─────────────────────────────────────
_PREFIXES: dict[str, dict[str, str]] = {
    "ka": {"pos": "PRON.1SG", "meaning": "I (1st person singular)"},
    "na": {"pos": "PRON.2SG", "meaning": "you (2nd person singular)"},
    "a": {"pos": "PRON.3SG", "meaning": "he/she/it (3rd person singular)"},
    "i": {"pos": "PRON.1PL.EXCL", "meaning": "we (1st person plural excl.)"},
    "ki": {"pos": "PRON.1PL.INCL", "meaning": "we (1st person plural incl.) / reflexive"},
}

# Ordered longest-first for greedy matching
# Only person agreement prefixes — not derivational (pi-, si- are part of roots)
_PREFIX_LIST: list[str] = ["ki", "ka", "na", "a", "i"]

# ── Suffixes (aspect/tense/nominalization/derivation) ─────────────────────────
_SUFFIXES: list[tuple[str, str, str]] = [
    # (suffix, POS tag, meaning)
    # NOTE: compound nouns like nuntakna, suahtakna are handled by compound detection
    ("takna", "NOUN", "state/quality (nominalizer)"),
    ("hna", "NOUN", "abstract noun (-hna)"),
    ("na", "NOUN", "nominalizer (-na)"),
    ("ma", "NOUN", "agent noun (-ma)"),
    ("mi", "NOUN", "agent noun (-mi)"),
    ("tang", "NOUN", "place noun (-tang)"),
    ("tung", "NOUN", "place noun (-tung)"),
    ("lan", "NOUN", "instrument (-lan)"),
    ("lam", "NOUN", "manner noun (-lam)"),
    ("thak", "NOUN", "thing noun (-thak)"),
    ("tak", "VERB", "completive aspect (-tak)"),
    ("sak", "VERB", "past tense (-sak)"),
    ("ah", "VERB", "progressive aspect (-ah)"),
    ("hen", "VERB", "experiential aspect (-hen)"),
    ("ding", "VERB", "future tense (-ding)"),
    ("zo", "VERB", "completive aspect (-zo)"),
    ("lai", "VERB", "progressive aspect (-lai)"),
    ("phei", "VERB", "benefactive (-phei)"),
    ("hia", "VERB", "past perfective (-hia)"),
    ("kia", "VERB", "past perfective (-kia)"),
    ("mang", "VERB", "desiderative (-mang)"),
    ("lo", "VERB", "negative (-lo)"),
]

# ── Common Zolai roots (from Bible + dictionary) ──────────────────────────────
_KNOWN_ROOTS: dict[str, dict[str, str]] = {
    "tak": {"pos": "VERB", "meaning": "walk/go"},
    "sak": {"pos": "VERB", "meaning": "write/sing"},
    "bawl": {"pos": "VERB", "meaning": "create/make"},
    "mu": {"pos": "VERB", "meaning": "see"},
    "ne": {"pos": "VERB", "meaning": "eat/drink"},
    "gen": {"pos": "VERB", "meaning": "know"},
    "pai": {"pos": "VERB", "meaning": "go"},
    "om": {"pos": "VERB", "meaning": "exist/be"},
    "hong": {"pos": "VERB", "meaning": "come"},
    "ci": {"pos": "VERB", "meaning": "say"},
    "he": {"pos": "VERB", "meaning": "give"},
    "dam": {"pos": "ADJ", "meaning": "well/healthy"},
    "siam": {"pos": "ADJ", "meaning": "good"},
    "khiang": {"pos": "ADJ", "meaning": "correct/true"},
    "gam": {"pos": "NOUN", "meaning": "earth/land"},
    "vantung": {"pos": "NOUN", "meaning": "heaven"},
    "leitung": {"pos": "NOUN", "meaning": "earth/world"},
    "tui": {"pos": "NOUN", "meaning": "water"},
    "mi": {"pos": "NOUN", "meaning": "person"},
    "numei": {"pos": "NOUN", "meaning": "woman"},
    "sing": {"pos": "NOUN", "meaning": "tree"},
    "pasian": {"pos": "NOUN", "meaning": "God"},
    "topa": {"pos": "NOUN", "meaning": "Lord"},
    "lai": {"pos": "NOUN", "meaning": "book/text"},
    "thu": {"pos": "NOUN", "meaning": "word/matter"},
    "kam": {"pos": "NOUN", "meaning": "work/deed"},
    "lam": {"pos": "NOUN", "meaning": "road/path"},
    "khua": {"pos": "NOUN", "meaning": "village/home"},
    "lungdam": {"pos": "NOUN", "meaning": "happiness/joy"},
    "nuntakna": {"pos": "NOUN", "meaning": "life"},
    "hehpihna": {"pos": "NOUN", "meaning": "salvation"},
    "suahtakna": {"pos": "NOUN", "meaning": "holiness"},
    "itna": {"pos": "NOUN", "meaning": "love"},
    "gupna": {"pos": "NOUN", "meaning": "faith"},
}

# ── Compound word patterns (van+tung, lei+tung) ──────────────────────────────
_COMPOUND_PARTS: dict[str, str] = {
    "van": "sky/heaven",
    "tung": "top/above",
    "lei": "ground/clay",
    "lai": "book/text",
    "siang": "clean/holy",
    "tho": "suffix (gentle)",
    "pa": "father",
    "sian": "great",
    "ta": "beginning",
    "khang": "time/era",
    "gui": "song",
    "thu": "word",
    "nam": "water",
    "hun": "body",
    "khem": "heart",
    "peuh": "mind",
}


class ZolaiMorphology:
    """Morphological analyzer for Tedim Zolai.

    Provides:
    - analyze(word): full morphological breakdown
    - lemmatize(word): base/root form
    - generate(root, prefix, suffix): word generation
    - is_compound(word): compound word detection

    Lazy-loads dictionary data on first use.
    """

    def __init__(self) -> None:
        self._dict_words: set[str] = set()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load data on first use."""
        if self._loaded:
            return
        self._loaded = True
        self._load_dict()

    def _load_dict(self) -> None:
        """Load dictionary headwords for validation."""
        dict_path = (
            _DATA_DIR / "dictionary" / "processed" / "dict_zo_en_master_v1.jsonl"
        )
        if not dict_path.exists():
            return
        try:
            import json as _json
            with open(dict_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = _json.loads(line)
                        word = str(entry.get("zolai", "")).lower().strip()
                        if word:
                            self._dict_words.add(word)
                    except (ValueError, TypeError):
                        continue
        except FileNotFoundError:
            return
        log.debug("Morphology: loaded %d dict words", len(self._dict_words))

    def analyze(self, word: str) -> dict[str, str | list[str]]:
        """Return morphological analysis of a Zolai word.

        Args:
            word: Zolai word to analyze.

        Returns:
            Dict with keys: stem, prefix, suffix, root, POS, morphemes, meaning.
            Example:
                analyze("nuntakna")
                → {'stem': 'nuntak', 'prefix': '', 'suffix': 'na',
                   'root': 'nun', 'POS': 'NOUN', 'morphemes': ['nun','tak','na'],
                   'meaning': 'life'}
        """
        self._ensure_loaded()
        lower = word.lower()
        clean = re.sub(r"[^\w]", "", lower)

        # Extract prefix
        prefix, stem = self._extract_prefix(clean)

        # Extract suffix
        stem_without_suffix, suffix, suffix_info = self._extract_suffix(stem)

        # Find root
        root = self._find_root(stem_without_suffix)

        # Determine POS
        pos = self._determine_pos(clean, suffix_info, root)

        # Get meaning
        meaning = self._get_meaning(clean, root)

        # Build morpheme list
        morphemes: list[str] = []
        if prefix:
            morphemes.append(prefix)
        morphemes.append(stem_without_suffix)
        if suffix:
            morphemes.append(suffix)

        return {
            "word": word,
            "stem": stem_without_suffix,
            "prefix": prefix,
            "suffix": suffix,
            "root": root,
            "POS": pos,
            "morphemes": morphemes,
            "meaning": meaning,
        }

    def lemmatize(self, word: str) -> str:
        """Return base/root form of a Zolai word.

        Strips known prefixes and suffixes to find the root.

        Args:
            word: Zolai word to lemmatize.

        Returns:
            Base form (root).
        """
        self._ensure_loaded()
        lower = word.lower()
        clean = re.sub(r"[^\w]", "", lower)

        # Strip prefix
        _, stem = self._extract_prefix(clean)

        # Strip suffix
        stem_no_suffix, _, _ = self._extract_suffix(stem)

        # Find root
        root = self._find_root(stem_no_suffix)

        return root

    def generate(self, root: str, prefix: str = "", suffix: str = "") -> str:
        """Generate a word from morphemes.

        Args:
            root: Base root form.
            prefix: Optional prefix (e.g. "ka", "na", "a").
            suffix: Optional suffix (e.g. "tak", "sak", "ding", "na").

        Returns:
            Generated Zolai word.
        """
        parts: list[str] = []
        if prefix:
            parts.append(prefix.lower())
        parts.append(root.lower())
        if suffix:
            parts.append(suffix.lower())
        return "".join(parts)

    def is_compound(self, word: str) -> dict[str, str | list[str]] | None:
        """Check if a word is a compound and decompose it.

        Args:
            word: Zolai word to check.

        Returns:
            Dict with 'parts' and 'meanings' if compound, else None.
        """
        lower = word.lower()

        # Check against known compound parts
        parts: list[str] = []
        meanings: list[str] = []
        remaining = lower

        # Greedy matching: sort parts by length (longest first)
        sorted_parts = sorted(_COMPOUND_PARTS.keys(), key=len, reverse=True)
        remaining = lower
        while remaining:
            matched = False
            for part in sorted_parts:
                if remaining.startswith(part):
                    parts.append(part)
                    meanings.append(_COMPOUND_PARTS[part])
                    remaining = remaining[len(part):]
                    matched = True
                    break  # restart from longest
            if not matched:
                break

        if len(parts) >= 2:
            return {
                "word": word,
                "parts": parts,
                "meanings": meanings,
                "compound_type": "head+modifier",
            }

        return None

    def get_prefixes(self) -> dict[str, dict[str, str]]:
        """Return all known prefixes."""
        return dict(_PREFIXES)

    def get_suffixes(self) -> list[tuple[str, str, str]]:
        """Return all known suffixes."""
        return list(_SUFFIXES)

    # ── Internal helpers ──────────────────────────────────────────────────
    def _extract_prefix(self, word: str) -> tuple[str, str]:
        """Extract prefix from word (longest match first).

        Only extracts if the remaining stem is at least 3 characters
        and matches a known root or common pattern.
        """
        for prefix in _PREFIX_LIST:
            if not word.startswith(prefix):
                continue
            stem_candidate = word[len(prefix):]
            if len(stem_candidate) < 3:
                continue
            # Only extract if stem is a known root or at least 4 chars
            # (prevents false matches like "piang" → "pi" + "angsak")
            if stem_candidate in _KNOWN_ROOTS or len(stem_candidate) >= 4:
                return prefix, stem_candidate
        return "", word

    def _extract_suffix(self, stem: str) -> tuple[str, str, Optional[dict[str, str]]]:
        """Extract suffix from stem (longest match first).

        Returns (stem_without_suffix, suffix, suffix_info).
        Requires stem to be at least 2 chars longer than suffix to avoid
        matching the entire word as a suffix.
        """
        for suffix, pos, meaning in _SUFFIXES:
            if stem.endswith(suffix) and len(stem) >= len(suffix) + 2:
                stem_without = stem[: -len(suffix)]
                return stem_without, suffix, {"pos": pos, "meaning": meaning}
        return stem, "", None

    def _find_root(self, stem: str) -> str:
        """Find the root of a stem by checking known roots."""
        # Direct match
        if stem in _KNOWN_ROOTS:
            return stem

        # Check if stem is a known root itself
        for root in _KNOWN_ROOTS:
            if stem.startswith(root) and len(root) >= 2:
                return root

        # Try removing common derivational suffixes iteratively
        current = stem
        for _ in range(3):
            for suffix, _, _ in _SUFFIXES:
                if current.endswith(suffix) and len(current) > len(suffix) + 1:
                    candidate = current[: -len(suffix)]
                    if candidate in _KNOWN_ROOTS:
                        return candidate
                    current = candidate
                    break
            else:
                break

        # Return as-is if no root found
        return stem

    def _determine_pos(self, word: str, suffix_info: Optional[dict], root: str) -> str:
        """Determine POS from suffix info and root."""
        if suffix_info:
            return suffix_info.get("pos", "X")

        if root in _KNOWN_ROOTS:
            return _KNOWN_ROOTS[root].get("pos", "X")

        if word in self._dict_words:
            return "KNOWN"

        return "X"

    def _get_meaning(self, word: str, root: str) -> str:
        """Get meaning from known roots."""
        if word in _KNOWN_ROOTS:
            return _KNOWN_ROOTS[word].get("meaning", "")
        if root in _KNOWN_ROOTS:
            return _KNOWN_ROOTS[root].get("meaning", "")
        return ""

    def get_stats(self) -> dict[str, int]:
        """Return morphology analyzer statistics."""
        self._ensure_loaded()
        return {
            "known_roots": len(_KNOWN_ROOTS),
            "prefixes": len(_PREFIXES),
            "suffixes": len(_SUFFIXES),
            "compound_parts": len(_COMPOUND_PARTS),
            "dict_words": len(self._dict_words),
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_morph: Optional[ZolaiMorphology] = None


def get_morphology() -> ZolaiMorphology:
    """Get or create the singleton morphology analyzer."""
    global _morph  # noqa: PLW0603
    if _morph is None:
        _morph = ZolaiMorphology()
    return _morph
