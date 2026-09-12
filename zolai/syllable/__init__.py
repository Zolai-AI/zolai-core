"""Zolai syllable segmentation engine — SylBreak4All.

Provides rule-based and CRF-based syllable segmentation for Tedim Zolai
(ZVS 2018 orthography). The engine handles digraphs, diphthongs, and
tone diacritics according to Zolai phonotactic constraints.

Quick start::

    from zolai.syllable import segment
    syllables = segment("pasian")   # ["pa", "sian"]

    from zolai.syllable import ZolaiSyllabifier
    syl = ZolaiSyllabifier(mode="rule")
    syllables = syl.segment("vantung")  # ["van", "tung"]
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Boundary",
    "ZolaiSyllabifier",
    "segment",
    "segment_with_boundaries",
]


@dataclass(frozen=True, slots=True)
class Boundary:
    """A single syllable boundary within a word.

    Attributes:
        start: Character index where this syllable begins (inclusive).
        end: Character index where this syllable ends (exclusive).
        syllable: The syllable text.
    """
    start: int
    end: int
    syllable: str


def segment(text: str) -> list[str]:
    """Segment a Zolai word into syllables using rule-based logic.

    Args:
        text: A single Zolai word (no whitespace).

    Returns:
        A list of syllable strings.

    Examples:
        >>> segment("pasian")
        ['pa', 'sian']
        >>> segment("vantung")
        ['van', 'tung']
    """
    return _default_syllabifier().segment(text)


def segment_with_boundaries(text: str) -> list[Boundary]:
    """Segment a Zolai word and return boundary metadata.

    Args:
        text: A single Zolai word (no whitespace).

    Returns:
        A list of Boundary objects with start/end positions.

    Examples:
        >>> bounds = segment_with_boundaries("gam")
        >>> [(b.start, b.end, b.syllable) for b in bounds]
        [(0, 3, 'gam')]
    """
    return _default_syllabifier().segment_with_boundaries(text)


def _default_syllabifier() -> ZolaiSyllabifier:
    """Create a default rule-based syllabifier (cached)."""
    return ZolaiSyllabifier(mode="rule")


class ZolaiSyllabifier:
    """Main syllable segmentation class.

    Supports two modes:

    - ``"rule"``: deterministic rule-based segmentation using Zolai
      phonotactic constraints.
    - ``"crf"``: CRF-based segmentation trained on gold data.

    Args:
        mode: ``"rule"`` or ``"crf"``.
    """

    def __init__(self, mode: str = "rule") -> None:
        if mode not in ("rule", "crf"):
            raise ValueError(f"mode must be 'rule' or 'crf', got {mode!r}")
        self.mode = mode
        self._segmenter = self._create_segmenter(mode)

    def _create_segmenter(self, mode: str):
        """Create the underlying segmenter implementation."""
        from .segmenter import CRFBasedSegmenter, RuleBasedSegmenter
        if mode == "rule":
            return RuleBasedSegmenter()
        return CRFBasedSegmenter()

    def segment(self, word: str) -> list[str]:
        """Segment a word into syllables.

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.
        """
        return self._segmenter.segment(word)

    def segment_with_boundaries(self, word: str) -> list[Boundary]:
        """Segment a word and return boundary metadata.

        Args:
            word: A single Zolai word.

        Returns:
            List of Boundary objects with positions.
        """
        syllables = self.segment(word)
        boundaries: list[Boundary] = []
        pos = 0
        for syl in syllables:
            boundaries.append(
                Boundary(start=pos, end=pos + len(syl), syllable=syl)
            )
            pos += len(syl)
        return boundaries

    def train(self, gold_data: list[tuple[str, list[str]]]) -> None:
        """Train the CRF segmenter on gold data.

        Args:
            gold_data: List of (word, syllables) pairs.

        Raises:
            RuntimeError: If mode is not ``"crf"``.
        """
        if self.mode != "crf":
            raise RuntimeError(
                "train() is only available in CRF mode"
            )
        self._segmenter.train(gold_data)

    def save(self, path: str) -> None:
        """Save the trained CRF model to disk.

        Args:
            path: File path for the serialized model.

        Raises:
            RuntimeError: If mode is not ``"crf"``.
        """
        if self.mode != "crf":
            raise RuntimeError(
                "save() is only available in CRF mode"
            )
        self._segmenter.save(path)

    def load(self, path: str) -> None:
        """Load a trained CRF model from disk.

        Args:
            path: File path of the serialized model.

        Raises:
            RuntimeError: If mode is not ``"crf"``.
        """
        if self.mode != "crf":
            raise RuntimeError(
                "load() is only available in CRF mode"
            )
        self._segmenter.load(path)
