"""Enhanced Morphological Analyzer — agglutinative decomposition with ZVS validation.

Extends existing zolai.morphology with:
- decompose(): full morpheme segmentation with directional/aspect/particle detection
- _detect_directional(): detect directional particles (hong, va, khia, lut, kik)
- _detect_aspect(): detect aspect markers (ta, zo, khin, lai, ding)
- _detect_particle(): detect particles (hi, hen, un, in, vo)
- _validate_zvs_morphemes(): ZVS 2018 compliance at morpheme level
- _validate_compound(): check compound components against known roots

Reference: zolai/morphology/__init__.py for existing _PREFIXES, _SUFFIXES, _KNOWN_PARTICLES.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from zolai.morphology import (
    _KNOWN_PARTICLES,
    _KNOWN_ROOTS,
    ZolaiMorphology,
    get_morphology,
)

log = logging.getLogger(__name__)


# ── ZVS 2018 forbidden forms at morpheme level ──────────────────────────────
_ZVS_FORBIDDEN_MORPHEMES: dict[str, str] = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
    "suah": "suahtakna",
    "nunnak": "nuntakna",
}

# ── Directional particles ────────────────────────────────────────────────────
_DIRECTIONALS: dict[str, str] = {
    "hong": "come (toward speaker)",
    "va": "go (away from speaker)",
    "khia": "go out/exit",
    "lut": "go in/enter",
    "kik": "return/come back",
}

# ── Aspect markers ───────────────────────────────────────────────────────────
_ASPECTS: dict[str, str] = {
    "ta": "completive/realized",
    "zo": "completive (finished)",
    "khin": "experiential (have done before)",
    "lai": "progressive (in process)",
    "ding": "future (will do)",
    "tak": "completive (alternative)",
    "ah": "progressive (alternative)",
}

# ── Sentence-final / clause particles ────────────────────────────────────────
_PARTICLES: dict[str, str] = {
    "hi": "declarative / present",
    "hen": "experiential / past",
    "un": "imperative / hortative",
    "in": "ergative / instrumental",
    "vo": "interrogative / evidential",
}


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class MorphemeAnalysis:
    """Detailed morpheme decomposition result."""
    segments: tuple[str, ...]
    directional: Optional[str]
    stem: str
    aspect: Optional[str]
    particle: Optional[str]
    is_valid: bool
    violations: tuple[str, ...] = field(default_factory=tuple)
    compound_parts: tuple[str, ...] = field(default_factory=tuple)
    prefix: str = ""
    suffix: str = ""


# ── EnhancedMorphologyAnalyzer ───────────────────────────────────────────────

class EnhancedMorphologyAnalyzer:
    """Enhanced morphological analyzer with agglutinative decomposition.

    Extends ZolaiMorphology with directional/aspect/particle detection,
    ZVS morpheme validation, and compound decomposition.
    """

    def __init__(self) -> None:
        self._base: Optional[ZolaiMorphology] = None

    @property
    def base(self) -> ZolaiMorphology:
        if self._base is None:
            self._base = get_morphology()
        return self._base

    def decompose(self, word: str) -> MorphemeAnalysis:
        """Full morpheme decomposition with directional/aspect/particle detection.

        Args:
            word: Zolai word to decompose.

        Returns:
            MorphemeAnalysis with all detected components.
        """
        clean = re.sub(r"[^\w]", "", word.lower())
        if not clean:
            return MorphemeAnalysis(
                segments=(), directional=None, stem="", aspect=None,
                particle=None, is_valid=False, violations=("empty word",),
            )

        # Get base analysis
        base_result = self.base.analyze(clean)

        # Detect components
        directional = self._detect_directional(clean)
        aspect = self._detect_aspect(clean)
        particle = self._detect_particle(clean)

        # Build segments
        segments = list(base_result.get("morphemes", [clean]))
        if directional and directional not in segments:
            segments.insert(0, directional)
        if aspect and aspect not in segments:
            segments.append(aspect)
        if particle and particle not in segments:
            segments.append(particle)

        # ZVS validation
        violations = self._validate_zvs_morphemes(segments)

        # Compound check
        compound_parts: tuple[str, ...] = ()
        if base_result.get("root") and "+" in base_result.get("root", ""):
            compound_parts = tuple(base_result["root"].split("+"))

        return MorphemeAnalysis(
            segments=tuple(segments),
            directional=directional,
            stem=base_result.get("stem", clean),
            aspect=aspect,
            particle=particle,
            is_valid=len(violations) == 0,
            violations=tuple(violations),
            compound_parts=compound_parts,
            prefix=base_result.get("prefix", ""),
            suffix=base_result.get("suffix", ""),
        )

    def _detect_directional(self, word: str) -> Optional[str]:
        """Detect directional particle at the beginning of a word.

        Directional particles: hong, va, khia, lut, kik
        They prefix verb stems to indicate movement direction.

        Args:
            word: Zolai word.

        Returns:
            Directional particle if found, else None.
        """
        for particle in _DIRECTIONALS:
            if word.startswith(particle) and len(word) > len(particle):
                # Verify remaining part is a plausible stem
                stem_candidate = word[len(particle):]
                if len(stem_candidate) >= 2:
                    return particle
        return None

    def _detect_aspect(self, word: str) -> Optional[str]:
        """Detect aspect marker at the end of a word.

        Aspect markers: ta, zo, khin, lai, ding, tak, ah

        Args:
            word: Zolai word.

        Returns:
            Aspect marker if found, else None.
        """
        # Check longest first
        for marker in sorted(_ASPECTS, key=len, reverse=True):
            if word.endswith(marker) and len(word) > len(marker):
                stem_candidate = word[: -len(marker)]
                if len(stem_candidate) >= 2:
                    return marker
        return None

    def _detect_particle(self, word: str) -> Optional[str]:
        """Detect sentence/clause particle at the end of a word.

        Particles: hi, hen, un, in, vo

        Args:
            word: Zolai word.

        Returns:
            Particle if found, else None.
        """
        # Check longest first
        for particle in sorted(_PARTICLES, key=len, reverse=True):
            if word.endswith(particle) and len(word) > len(particle):
                stem_candidate = word[: -len(particle)]
                if len(stem_candidate) >= 2:
                    return particle
        # Check if the whole word is a particle
        if word in _KNOWN_PARTICLES:
            return word
        return None

    def _validate_zvs_morphemes(self, segments: tuple[str, ...]) -> list[str]:
        """Validate morphemes against ZVS 2018 rules.

        Checks each segment for forbidden deprecated forms.

        Args:
            segments: Tuple of morpheme strings.

        Returns:
            List of violation descriptions.
        """
        violations: list[str] = []
        for seg in segments:
            lower = seg.lower()
            if lower in _ZVS_FORBIDDEN_MORPHEMES:
                correct = _ZVS_FORBIDDEN_MORPHEMES[lower]
                violations.append(
                    f"Forbidden morpheme '{lower}' → use '{correct}'"
                )
        return violations

    def _validate_compound(self, word: str) -> tuple[bool, tuple[str, ...]]:
        """Check compound components against known roots.

        Args:
            word: Zolai compound word.

        Returns:
            (is_valid, tuple of validation notes).
        """
        notes: list[str] = []
        base_result = self.base.analyze(word)
        root = base_result.get("root", "")

        if "+" not in root:
            return True, ()

        parts = root.split("+")
        for part in parts:
            if part not in _KNOWN_ROOTS and part not in _KNOWN_PARTICLES:
                notes.append(f"Unknown compound component: '{part}'")
            elif part in _ZVS_FORBIDDEN_MORPHEMES:
                correct = _ZVS_FORBIDDEN_MORPHEMES[part]
                notes.append(f"Forbidden compound part '{part}' → use '{correct}'")

        return len(notes) == 0, tuple(notes)

    def get_directionals(self) -> dict[str, str]:
        """Return all directional particles with descriptions."""
        return dict(_DIRECTIONALS)

    def get_aspects(self) -> dict[str, str]:
        """Return all aspect markers with descriptions."""
        return dict(_ASPECTS)

    def get_particles(self) -> dict[str, str]:
        """Return all sentence/clause particles with descriptions."""
        return dict(_PARTICLES)


# ── Module-level singleton ──────────────────────────────────────────────────
_enhanced_morph: Optional[EnhancedMorphologyAnalyzer] = None


def get_enhanced_morphology() -> EnhancedMorphologyAnalyzer:
    """Get or create the singleton EnhancedMorphologyAnalyzer."""
    global _enhanced_morph  # noqa: PLW0603
    if _enhanced_morph is None:
        _enhanced_morph = EnhancedMorphologyAnalyzer()
    return _enhanced_morph
