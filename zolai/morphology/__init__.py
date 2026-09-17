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

# ── Zolai Tone System (4 tones like Chinese) ──────────────────────────────────
# T1 = High, T2 = High Falling (sandhi only), T3 = Low, T4 = Creaky
# Written Zolai does NOT mark tones. Same spelling can have different meanings.
# Tone sandhi rules change tones in compound words (19 rules).
# Reference: data/reference/grammar/lesson_02_Tone_Sandhi_Tedim_Zomi_Toponyms.md

_TONE_NOTES: dict[str, str] = {
    "khem": "T1=lie/deceive, T3=thin/weak",
    "nam": "T1=smell, T3=odoriferous",
    "zu": "T1=alcohol/distillate, T4=rain (with guah-)",
    "ta": "T1=completive aspect, T3=beginning",
    "van": "T1=sky/heaven, T3=thing/goods",
    "gu": "T1=bone, T4=steal",
    "sa": "T1=meat/flesh, T4=hot/feeling hot",
    "siang": "T1=call/summon, T3=clean/holy (in compounds)",
    "ci": "T1=say/speak",
    "ne": "T1=eat/drink",
    "pai": "T1=go/move",
    "om": "T1=exist/stay",
}

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
    # === VERBS ===
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
    "siang": {"pos": "VERB", "meaning": "call/summon; clean/holy (in compounds)"},
    "tho": {"pos": "VERB", "meaning": "fly"},
    "tung": {"pos": "VERB", "meaning": "arrive; top/above (in compounds)"},
    "lei": {"pos": "VERB", "meaning": "buy/purchase; ground/clay (in compounds)"},
    # === ADJECTIVES ===
    "dam": {"pos": "ADJ", "meaning": "well/healthy"},
    "siam": {"pos": "ADJ", "meaning": "good"},
    "khiang": {"pos": "ADJ", "meaning": "correct/true"},
    "nam": {"pos": "VERB", "meaning": "smell; odoriferous (tone-dependent)"},
    "khem": {"pos": "VERB", "meaning": "lie/deceive; thin/weak (tone-dependent)"},
    # === ADVERBS ===
    "peuh": {"pos": "ADV", "meaning": "ever/always"},
    # === NOUNS ===
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
    "lungdam": {"pos": "NOUN", "meaning": "heart/mind"},
    "nuntakna": {"pos": "NOUN", "meaning": "life"},
    "hehpihna": {"pos": "NOUN", "meaning": "grace/mercy"},
    "suahtakna": {"pos": "NOUN", "meaning": "independence/autonomy"},
    "itna": {"pos": "NOUN", "meaning": "love"},
    "gupna": {"pos": "NOUN", "meaning": "faith"},
    "zu": {"pos": "NOUN", "meaning": "distillate (alcohol)"},
    "guahzu": {"pos": "NOUN", "meaning": "rain", "compound": ["guah", "zu"]},
    "hun": {"pos": "NOUN", "meaning": "fortune/time"},
    "gui": {"pos": "NOUN", "meaning": "order/regulation"},
    "khang": {"pos": "NOUN", "meaning": "generation/era"},
    "sian": {"pos": "NOUN", "meaning": "great (in compounds); cleanse (standalone)"},
    "van": {"pos": "NOUN", "meaning": "sky/heaven; thing/goods"},
    # === DET ===
    "khempeuh": {"pos": "DET", "meaning": "all/every/whole"},
    "laisiangtho": {"pos": "NOUN", "meaning": "Bible"},
    "khuapi": {"pos": "NOUN", "meaning": "town/city"},
    "singgui": {"pos": "NOUN", "meaning": "root"},
    "thagui": {"pos": "NOUN", "meaning": "artery"},
    "guihna": {"pos": "NOUN", "meaning": "inebriety"},
    # === PARTICLES ===
    "ta": {"pos": "PART", "meaning": "completive/realized aspect"},
}

# ── Compound word patterns (van+tung, lei+tung) ──────────────────────────────
_COMPOUND_PARTS: dict[str, str] = {
    "van": "sky/heaven",
    "tung": "top/above",
    "lei": "ground/clay",
    "lai": "book/text",
    "siang": "call/summon; clean/holy",
    "tho": "fly",
    "pa": "father",
    "sian": "great",
    "ta": "completive/realized",
    "khang": "generation",
    "gui": "order/regulation",
    "thu": "word/matter",
    "nam": "smell; odoriferous",
    "hun": "fortune/time",
    "khem": "lie/deceive; thin/weak",
    "peuh": "ever/always",
    "guah": "rain",
    "zu": "distillate",
}

# ── High-frequency roots from Bible (top 100 words) ───────────────────────────
_HIGH_FREQ_ROOTS: dict[str, dict[str, str]] = {
    # Verbs
    "ahih": {"pos": "VERB", "meaning": "be/exist"},
    "ahi": {"pos": "VERB", "meaning": "be/exist"},
    "bawl": {"pos": "VERB", "meaning": "create/make"},
    "gen": {"pos": "VERB", "meaning": "know/understand"},
    "pai": {"pos": "VERB", "meaning": "go/move"},
    "om": {"pos": "VERB", "meaning": "exist/stay"},
    "pia": {"pos": "VERB", "meaning": "give"},
    "mu": {"pos": "VERB", "meaning": "see/look"},
    "thei": {"pos": "VERB", "meaning": "know"},
    "kong": {"pos": "VERB", "meaning": "say/speak"},
    "nei": {"pos": "VERB", "meaning": "have/possess"},
    "za": {"pos": "VERB", "meaning": "drink"},
    "nek": {"pos": "VERB", "meaning": "eat"},
    "huap": {"pos": "VERB", "meaning": "include/cover/span"},
    "ne": {"pos": "VERB", "meaning": "eat/drink"},
    "dam": {"pos": "VERB", "meaning": "be well/healthy"},
    "sak": {"pos": "VERB", "meaning": "write/sing"},
    "tak": {"pos": "VERB", "meaning": "walk/go"},
    "hong": {"pos": "VERB", "meaning": "come"},
    "ci": {"pos": "VERB", "meaning": "say/speak"},
    "he": {"pos": "VERB", "meaning": "give"},
    "ciang": {"pos": "VERB", "meaning": "begin/start"},
    "lam": {"pos": "VERB", "meaning": "cross/pass"},
    "kik": {"pos": "VERB", "meaning": "return/come back"},
     "siang": {"pos": "VERB", "meaning": "call/summon; clean/holy (in compounds)"},
     # Nouns
     "pasian": {"pos": "NOUN", "meaning": "God"},
     "topa": {"pos": "NOUN", "meaning": "Lord/master"},
     "tapa": {"pos": "NOUN", "meaning": "son/life"},
     "gam": {"pos": "NOUN", "meaning": "country/earth"},
     "khua": {"pos": "NOUN", "meaning": "village/place"},
     "khuapi": {"pos": "NOUN", "meaning": "city"},
     "mi": {"pos": "NOUN", "meaning": "person"},
     "lungsim": {"pos": "NOUN", "meaning": "heart/mind"},
     "nuntakna": {"pos": "NOUN", "meaning": "life"},
     "theihna": {"pos": "NOUN", "meaning": "knowledge"},
     "biakna": {"pos": "NOUN", "meaning": "worship"},
     "vantung": {"pos": "NOUN", "meaning": "heaven"},
     "leitung": {"pos": "NOUN", "meaning": "earth/world"},
     "tui": {"pos": "NOUN", "meaning": "water"},
     "numei": {"pos": "NOUN", "meaning": "woman"},
     "sing": {"pos": "NOUN", "meaning": "tree"},
     "lai": {"pos": "NOUN", "meaning": "book/text"},
     "thu": {"pos": "NOUN", "meaning": "word/matter"},
     "kam": {"pos": "NOUN", "meaning": "work/deed"},
     "lungdam": {"pos": "NOUN", "meaning": "heart/mind"},
     "hehpihna": {"pos": "NOUN", "meaning": "grace/mercy"},
     "suahtakna": {"pos": "NOUN", "meaning": "independence/autonomy"},
     "itna": {"pos": "NOUN", "meaning": "love"},
     "gupna": {"pos": "NOUN", "meaning": "faith"},
     "kumpipa": {"pos": "NOUN", "meaning": "Savior"},
     "hun": {"pos": "NOUN", "meaning": "fortune/time"},
    "u": {"pos": "NOUN", "meaning": "elder brother/sister"},
    "nau": {"pos": "NOUN", "meaning": "younger brother/sister"},
    # Adjectives
    "siam": {"pos": "ADJ", "meaning": "good"},
    "hoih": {"pos": "ADJ", "meaning": "good"},
    "lian": {"pos": "ADJ", "meaning": "big"},
    "khiang": {"pos": "ADJ", "meaning": "correct/true"},
    # Adverbs
    "teng": {"pos": "ADV", "meaning": "up/above"},
    "mahmah": {"pos": "ADV", "meaning": "very"},
    # Pronouns
    "bang": {"pos": "PRON", "meaning": "what/how"},
    "hang": {"pos": "PRON", "meaning": "where/how"},
    "ama": {"pos": "PRON", "meaning": "he/she/it"},
    "amau": {"pos": "PRON", "meaning": "they"},
    # Postpositions
    "kiangah": {"pos": "POST", "meaning": "in/at"},
    "sungah": {"pos": "POST", "meaning": "in/within"},
}

# Merge high-frequency roots into _KNOWN_ROOTS
_KNOWN_ROOTS.update(_HIGH_FREQ_ROOTS)

# ── Ergative and clause-boundary particles ─────────────────────────────────────
_KNOWN_PARTICLES: dict[str, dict[str, str]] = {
    "in": {"pos": "PART.ERG", "meaning": "ergative particle"},
    "leh": {"pos": "PART.CONJ", "meaning": "and"},
    "tawh": {"pos": "PART.CONJ", "meaning": "with"},
    "ciangin": {"pos": "PART.CONJ", "meaning": "before"},
    "tungah": {"pos": "PART.POST", "meaning": "on/above"},
    "sungah": {"pos": "PART.POST", "meaning": "in/within"},
    "kiangah": {"pos": "PART.POST", "meaning": "at/near"},
}

# Particles sorted longest-first for greedy splitting
_PARTICLE_LIST: list[str] = sorted(_KNOWN_PARTICLES.keys(), key=len, reverse=True)


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

        Analyzes in order:
        1. Check if the whole word is a known particle (in, leh, tawh, etc.)
        2. Check if root + particle (compound like huapin → huap + in)
        3. Extract prefix (ka-, na-, a-, i-, ki-)
        4. Extract suffix (-na, -tak, -sak, -ah, -hen, -ding, -lo)
        5. Find root from known roots

        Args:
            word: Zolai word to analyze.

        Returns:
            Dict with keys: word, stem, prefix, suffix, root, POS, morphemes,
            meaning, particle.
        """
        self._ensure_loaded()
        lower = word.lower()
        clean = re.sub(r"[^\w]", "", lower)

        # 1. Check if whole word is a known particle
        if clean in _KNOWN_PARTICLES:
            info = _KNOWN_PARTICLES[clean]
            meaning = self._apply_tone_note(info["meaning"], clean, clean)
            return {
                "word": word,
                "stem": clean,
                "prefix": "",
                "suffix": "",
                "root": clean,
                "POS": info["pos"],
                "morphemes": [clean],
                "meaning": meaning,
                "particle": clean,
            }

        # 1b. Check for compound words (laisiangtho → lai+siang+tho)
        compound = self.is_compound(clean)
        if compound:
            parts = compound["parts"]
            meanings = compound["meanings"]
            # Build morphemes from parts
            morphemes = []
            root_parts = []
            for part, meaning in zip(parts, meanings):
                morphemes.append(part)
                root_parts.append(f"{part}({meaning.split(';')[0].split('(')[0].strip()})"
                )
            # Primary root is the last part (head of compound)
            compound_meaning = "; ".join(meanings)
            compound_meaning = self._apply_tone_note(compound_meaning, clean, clean)
            return {
                "word": word,
                "stem": clean,
                "prefix": "",
                "suffix": "",
                "root": "+".join(parts),
                "POS": "NOUN",
                "morphemes": morphemes,
                "meaning": compound_meaning,
                "particle": "",
            }

        # 1c. Check for reduplication (namnam → nam+nam, khemkhem → khem+khem)
        if len(clean) >= 4:
            half = len(clean) // 2
            if len(clean) % 2 == 0 and clean[:half] == clean[half:]:
                base = clean[:half]
                # Check if base is in _KNOWN_ROOTS or dictionary
                if base in _KNOWN_ROOTS:
                    root_info = _KNOWN_ROOTS[base]
                    redup_meaning = root_info.get("meaning", "") + " (reduplicated)"
                    redup_meaning = self._apply_tone_note(redup_meaning, base, base)
                    return {
                        "word": word,
                        "stem": clean,
                        "prefix": "",
                        "suffix": "",
                        "root": base,
                        "POS": root_info.get("pos", "X"),
                        "morphemes": [base, base],
                        "meaning": redup_meaning,
                        "particle": "",
                    }

        # 2. Try splitting off a trailing particle (huapin → huap + in)
        root_part, particle = self._split_particle(clean)
        if particle:
            # Analyze the root portion
            prefix, stem = self._extract_prefix(root_part)
            stem_no_suffix, suffix, suffix_info = self._extract_suffix(stem)
            root = self._find_root(stem_no_suffix)
            pos = self._determine_pos(root_part, suffix_info, root)
            meaning = self._get_meaning(root_part, root)
            meaning = self._apply_tone_note(meaning, root_part, root)
            morphemes: list[str] = []
            if prefix:
                morphemes.append(prefix)
            morphemes.append(stem_no_suffix)
            if suffix:
                morphemes.append(suffix)
            morphemes.append(particle)
            return {
                "word": word,
                "stem": stem_no_suffix,
                "prefix": prefix,
                "suffix": suffix,
                "root": root,
                "POS": pos,
                "morphemes": morphemes,
                "meaning": meaning,
                "particle": particle,
            }

        # 3. Standard prefix → suffix → root analysis
        prefix, stem = self._extract_prefix(clean)

        # 3a. If the full word (or stem after prefix) is a known root,
        #     don't strip suffixes — it's a complete lexical item
        #     (e.g. mahmah should not be split into mahm + ah)
        if stem in _KNOWN_ROOTS:
            root_info = _KNOWN_ROOTS[stem]
            root_meaning = root_info.get("meaning", "")
            root_meaning = self._apply_tone_note(root_meaning, stem, stem)
            return {
                "word": word,
                "stem": stem,
                "prefix": prefix,
                "suffix": "",
                "root": stem,
                "POS": root_info.get("pos", "X"),
                "morphemes": [prefix, stem] if prefix else [stem],
                "meaning": root_meaning,
                "particle": "",
            }

        stem_without_suffix, suffix, suffix_info = self._extract_suffix(stem)
        root = self._find_root(stem_without_suffix)
        pos = self._determine_pos(clean, suffix_info, root)
        meaning = self._get_meaning(clean, root)
        meaning = self._apply_tone_note(meaning, clean, root)

        morphemes_std: list[str] = []
        if prefix:
            morphemes_std.append(prefix)
        morphemes_std.append(stem_without_suffix)
        if suffix:
            morphemes_std.append(suffix)

        # Add tone note if available
        tone_note = _TONE_NOTES.get(root, "")
        if tone_note and tone_note not in meaning:
            meaning = f"{meaning} [tones: {tone_note}]" if meaning else f"[tones: {tone_note}]"

        return {
            "word": word,
            "stem": stem_without_suffix,
            "prefix": prefix,
            "suffix": suffix,
            "root": root,
            "POS": pos,
            "morphemes": morphemes_std,
            "meaning": meaning,
            "particle": "",
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
        """Get meaning for word from known roots or dictionary."""
        meaning = ""
        if word in _KNOWN_ROOTS:
            meaning = _KNOWN_ROOTS[word].get("meaning", "")
        if not meaning and root in _KNOWN_ROOTS:
            meaning = _KNOWN_ROOTS[root].get("meaning", "")
        # Dynamic dictionary lookup (headwords set)
        if not meaning and self._dict_words:
            if word in self._dict_words or root in self._dict_words:
                meaning = f"{word or root} (from dictionary)"
        return meaning

    def _apply_tone_note(self, meaning: str, word: str, root: str) -> str:
        """Append tone-dependency note if the word/root is tone-dependent.

        Zolai has 4 tones (T1=High, T2=HighFalling, T3=Low, T4=Creaky).
        Written Zolai does not mark tones, so the same spelling can have
        different meanings. This method tags tone-dependent words so callers
        know disambiguation is needed.

        Args:
            meaning: Current meaning string.
            word: The full word analyzed.
            root: The root morpheme.

        Returns:
            Meaning with tone note appended, if applicable.
        """
        tone_note = _TONE_NOTES.get(word, "") or _TONE_NOTES.get(root, "")
        if tone_note and tone_note not in meaning:
            if meaning:
                return f"{meaning} [tones: {tone_note}]"
            return f"[tones: {tone_note}]"
        return meaning

    def _split_particle(self, word: str) -> tuple[str, str]:
        """Split word into root + particle if the word ends with a known particle.

        E.g. 'huapin' → ('huap', 'in'), 'kam leh' → ('kam', 'leh').

        Only splits if:
        - The root portion is at least 3 characters, OR
        - The root portion matches a known root.

        Returns:
            (root_part, particle) — if no particle found, returns (word, '').
        """
        for particle in _PARTICLE_LIST:
            if word.endswith(particle) and len(word) > len(particle):
                root_candidate = word[: -len(particle)]
                if root_candidate in _KNOWN_ROOTS or len(root_candidate) >= 3:
                    return root_candidate, particle
        return word, ""

    def get_stats(self) -> dict[str, int]:
        """Return morphology analyzer statistics."""
        self._ensure_loaded()
        return {
            "known_roots": len(_KNOWN_ROOTS),
            "high_freq_roots": len(_HIGH_FREQ_ROOTS),
            "particles": len(_KNOWN_PARTICLES),
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
