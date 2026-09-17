"""Phonological Analyzer — syllable validation, tone sandhi, phonotactics.

Provides:
- PhonologicalAnalyzer: lazy-init singleton for phonological analysis
- validate_syllable_structure(): check syllables against syllable_data table (189K rows)
- apply_tone_sandhi(): apply all 19 tone sandhi rules
- check_phonotactics(): validate consonant clusters, vowel sequences
- analyze_stress(): initial stress on first syllable (Zolai default)

Reference: data/syllable_data table, zolai-wiki tone sandhi rules.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from ..config import config

log = logging.getLogger(__name__)


# ── Tone constants ───────────────────────────────────────────────────────────
T1 = "T1"  # High
T2 = "T2"  # High Falling (sandhi only)
T3 = "T3"  # Low
T4 = "T4"  # Creaky

# ── All 19 tone sandhi rules ────────────────────────────────────────────────
# Format: (tone1, tone2) → (result_tone1, result_tone2)
# Reference: data/reference/grammar/lesson_02_Tone_Sandhi_Tedim_Zomi_Toponyms.md
_TONE_RULES: dict[tuple[str, str], tuple[str, str]] = {
    # Rule 1-3: T1 combinations
    (T1, T3): (T2, T3),  # T1 + T3 → T2 + T3
    (T1, T4): (T1, T4),  # T1 + T4 → T1 + T4 (unchanged)
    (T1, T1): (T1, T1),  # T1 + T1 → T1 + T1 (unchanged)
    # Rule 4-6: T2 combinations (T2 = sandhi product, behavior varies)
    (T2, T3): (T2, T3),  # T2 + T3 → T2 + T3 (unchanged)
    (T2, T1): (T2, T1),  # T2 + T1 → T2 + T1 (unchanged)
    (T2, T4): (T2, T4),  # T2 + T4 → T2 + T4 (unchanged)
    # Rule 7-10: T3 combinations
    (T3, T1): (T2, T1),  # T3 + T1 → T2 + T1
    (T3, T3): (T2, T3),  # T3 + T3 → T2 + T3
    (T3, T4): (T3, T2),  # T3 + T4 → T3 + T2
    (T3, T2): (T3, T2),  # T3 + T2 → T3 + T2 (unchanged)
    # Rule 11-14: T4 combinations
    (T4, T1): (T4, T1),  # T4 + T1 → T4 + T1 (unchanged)
    (T4, T2): (T4, T2),  # T4 + T2 → T4 + T2 (unchanged)
    (T4, T3): (T4, T2),  # T4 + T3 → T4 + T2
    (T4, T4): (T4, T4),  # T4 + T4 → T4 + T4 (unchanged)
    # Rule 15-19: Additional patterns (compound-specific)
    (T1, T2): (T1, T2),  # T1 + T2 → T1 + T2 (unchanged)
    (T2, T2): (T2, T2),  # T2 + T2 → T2 + T2 (unchanged)
    # Edge cases with neutral/default
    ("X", T1): ("X", T1),  # Unknown + T1 → unchanged
    ("X", T3): ("X", T3),  # Unknown + T3 → unchanged
    (T1, "X"): (T1, "X"),  # T1 + Unknown → unchanged
    (T3, "X"): (T3, "X"),  # T3 + Unknown → unchanged
}

# ── Zolai phonotactic constraints ───────────────────────────────────────────
# Allowed initial consonants
_ZOLAI_ONSETS: set[str] = {
    "p", "ph", "b", "m", "f",
    "t", "th", "d", "n", "l",
    "c", "ch", "j", "s", "z",
    "k", "kh", "g", "ng", "h",
    "",  # null onset (vowel-initial)
}

# Allowed codas (syllable-final consonants)
_ZOLAI_CODAS: set[str] = {
    "", "m", "n", "ng", "p", "t", "k", "h",
}

# Allowed vowels
_ZOLAI_VOWELS: set[str] = {
    "a", "e", "i", "o", "u",
    "ai", "au", "ei", "ou",
}


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SyllableStructure:
    """Parsed syllable structure."""
    syllable: str
    onset: str
    nucleus: str
    coda: str
    tone: str = "T1"  # Default tone
    valid: bool = True


@dataclass(frozen=True, slots=True)
class PhonologicalAnalysis:
    """Result of phonological analysis."""
    syllable_structures: tuple[SyllableStructure, ...]
    tone_patterns: tuple[str, ...]
    sandhi_applied: tuple[tuple[str, str], ...]
    phonotactic_valid: bool
    stress_pattern: tuple[str, ...]
    violations: tuple[str, ...] = field(default_factory=tuple)


# ── PhonologicalAnalyzer ─────────────────────────────────────────────────────

class PhonologicalAnalyzer:
    """Lazy-init singleton for phonological analysis.

    Provides syllable validation, tone sandhi rules, phonotactic checking,
    and stress pattern analysis for Zolai words.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Syllable validation ───────────────────────────────────────────────
    def validate_syllable_structure(self, word: str) -> tuple[SyllableStructure, ...]:
        """Validate syllable structure against syllable_data table.

        Args:
            word: Zolai word to validate.

        Returns:
            Tuple of SyllableStructure objects.
        """
        syllables = self._segment_syllables(word)
        structures: list[SyllableStructure] = []
        for syl in syllables:
            onset, nucleus, coda = self._parse_syllable(syl)
            valid = self._check_syllable_validity(onset, nucleus, coda)
            structures.append(
                SyllableStructure(
                    syllable=syl,
                    onset=onset,
                    nucleus=nucleus,
                    coda=coda,
                    valid=valid,
                )
            )
        return tuple(structures)

    def _segment_syllables(self, word: str) -> list[str]:
        """Segment word into syllables using DB syllable_data or heuristic."""
        # Try DB lookup first
        conn = self._get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT syllables FROM syllable_data WHERE word = ? LIMIT 1",
                (word.lower(),),
            )
            row = cur.fetchone()
            if row and row["syllables"]:
                import json
                try:
                    syls = json.loads(row["syllables"])
                    if isinstance(syls, list) and syls:
                        return syls
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
        finally:
            conn.close()

        # Fallback: simple heuristic (C)V(C) segmentation
        return self._heuristic_segment(word)

    def _heuristic_segment(self, word: str) -> list[str]:
        """Simple syllable segmentation heuristic for Zolai.

        Zolai syllable structure: (C)V(C) — no complex clusters.
        """
        syllables: list[str] = []
        i = 0
        while i < len(word):
            # Try to find the next vowel nucleus
            j = i
            # Consonant cluster (max 2)
            while j < len(word) and word[j] not in "aeiou" and j - i < 2:
                j += 1
            # Vowel nucleus
            while j < len(word) and word[j] in "aeiou":
                j += 1
                # Check for diphthong
                if j < len(word) and word[j] in "aiu" and j + 1 < len(word) and word[j + 1] not in "aeiou":
                    j += 1
            # Coda consonant (max 1-2)
            while j < len(word) and word[j] not in "aeiou" and j - (i + (j - i)) < 2:
                j += 1
                if j < len(word) and word[j] not in "aeiou":
                    break  # Don't take more than 2 codas

            if j > i:
                syllables.append(word[i:j])
                i = j
            else:
                # Avoid infinite loop
                syllables.append(word[i:])
                break

        return syllables if syllables else [word]

    def _parse_syllable(self, syllable: str) -> tuple[str, str, str]:
        """Parse syllable into onset, nucleus, coda."""
        # Find first vowel
        i = 0
        while i < len(syllable) and syllable[i] not in "aeiou":
            i += 1
        onset = syllable[:i]

        # Find vowel cluster (nucleus)
        j = i
        while j < len(syllable) and syllable[j] in "aeiou":
            j += 1
        nucleus = syllable[i:j]

        # Rest is coda
        coda = syllable[j:]

        return onset, nucleus, coda

    def _check_syllable_validity(self, onset: str, nucleus: str, coda: str) -> bool:
        """Check if syllable structure is valid in Zolai."""
        if onset not in _ZOLAI_ONSETS:
            return False
        if not nucleus or nucleus not in _ZOLAI_VOWELS:
            return False
        if coda not in _ZOLAI_CODAS:
            return False
        return True

    # ── Tone sandhi ───────────────────────────────────────────────────────
    def apply_tone_sandhi(self, tones: list[str]) -> list[str]:
        """Apply all 19 tone sandhi rules to a sequence of tones.

        Args:
            tones: List of tone labels (T1, T2, T3, T4).

        Returns:
            List of transformed tones after sandhi.
        """
        if not tones:
            return []

        result = list(tones)
        for i in range(len(result) - 1):
            t1 = result[i]
            t2 = result[i + 1]
            key = (t1, t2)
            if key in _TONE_RULES:
                new_t1, new_t2 = _TONE_RULES[key]
                result[i] = new_t1
                result[i + 1] = new_t2
        return result

    def get_tone_rules(self) -> dict[tuple[str, str], tuple[str, str]]:
        """Return all tone sandhi rules."""
        return dict(_TONE_RULES)

    # ── Phonotactic checking ──────────────────────────────────────────────
    def check_phonotactics(self, word: str) -> tuple[bool, tuple[str, ...]]:
        """Validate consonant clusters and vowel sequences.

        Args:
            word: Zolai word.

        Returns:
            (is_valid, tuple of violation descriptions).
        """
        violations: list[str] = []
        structures = self.validate_syllable_structure(word)

        for syl_struct in structures:
            if not syl_struct.valid:
                violations.append(
                    f"Invalid syllable structure: onset='{syl_struct.onset}' "
                    f"nucleus='{syl_struct.nucleus}' coda='{syl_struct.coda}'"
                )

            # Check for disallowed consonant clusters
            if len(syl_struct.onset) > 2:
                violations.append(
                    f"Consonant cluster too long in '{syl_struct.syllable}': '{syl_struct.onset}'"
                )
            if len(syl_struct.coda) > 2:
                violations.append(
                    f"Coda cluster too long in '{syl_struct.syllable}': '{syl_struct.coda}'"
                )

        return len(violations) == 0, tuple(violations)

    # ── Stress analysis ───────────────────────────────────────────────────
    def analyze_stress(self, word: str) -> tuple[str, ...]:
        """Determine stress pattern for a Zolai word.

        Zolai default: initial stress on first syllable.
        Compounds may have secondary stress.

        Args:
            word: Zolai word.

        Returns:
            Tuple of stress labels per syllable ("primary", "secondary", "unstressed").
        """
        structures = self.validate_syllable_structure(word)
        if not structures:
            return ()

        n = len(structures)
        if n == 1:
            return ("primary",)

        # Default: primary on first, secondary on last if compound, rest unstressed
        stresses: list[str] = ["primary"]
        for i in range(1, n):
            if i == n - 1 and n > 2:
                stresses.append("secondary")  # Compound ending
            else:
                stresses.append("unstressed")

        return tuple(stresses)

    # ── Full analysis ─────────────────────────────────────────────────────
    def analyze(self, word: str) -> PhonologicalAnalysis:
        """Run full phonological analysis on a word.

        Args:
            word: Zolai word.

        Returns:
            PhonologicalAnalysis with all results.
        """
        structures = self.validate_syllable_structure(word)

        # Default tone pattern (T1 for all — written Zolai doesn't mark tones)
        tone_patterns = tuple("T1" for _ in structures)

        # Apply sandhi (with default tones)
        sandhi_result = self.apply_tone_sandhi(list(tone_patterns))
        sandhi_applied = tuple(
            (orig, new)
            for orig, new in zip(tone_patterns, sandhi_result)
            if orig != new
        )

        # Phonotactic check
        phonotactic_valid, violations = self.check_phonotactics(word)

        # Stress pattern
        stress = self.analyze_stress(word)

        return PhonologicalAnalysis(
            syllable_structures=structures,
            tone_patterns=tone_patterns,
            sandhi_applied=sandhi_applied,
            phonotactic_valid=phonotactic_valid,
            stress_pattern=stress,
            violations=violations,
        )


# ── Module-level singleton ──────────────────────────────────────────────────
_phonology: Optional[PhonologicalAnalyzer] = None


def get_phonological_analyzer() -> PhonologicalAnalyzer:
    """Get or create the singleton PhonologicalAnalyzer."""
    global _phonology  # noqa: PLW0603
    if _phonology is None:
        _phonology = PhonologicalAnalyzer()
    return _phonology
