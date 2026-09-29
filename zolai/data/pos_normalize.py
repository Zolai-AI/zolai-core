"""Normalize legacy free-text POS labels into canonical UPOS values (L1.3).

The three lexicon tables (``dictionary``, ``vocabulary``, ``zolai_vocabulary``)
carry a legacy ``pos`` column written by many pipelines over the years.  Its
vocabulary is inconsistent (``noun`` / ``Noun`` / ``n``, multi-label strings
such as ``adv & a``, and junk such as ``pt par`` or ``pas. part.``).

This module is **pure** (no I/O, no database): it maps one legacy label onto

* a single canonical UPOS tag from the 17-tag Universal Dependencies
  allowlist (:data:`UPOS_ALLOWLIST`), or
* an ordered list of candidate UPOS tags when the label is ambiguous
  (``conjunction`` → CCONJ/SCONJ), or
* nothing at all (``canonical is None``, ``evidence == "unknown"``) when the
  label is an affix marker, a junk token, or simply unmapped.

The original ``pos`` value is never modified — normalization is written to the
additive ``pos_canonical`` / ``pos_candidates`` / ``pos_evidence`` columns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

__all__ = [
    "CANDIDATE_CONFIDENCE",
    "EVIDENCE_VALUES",
    "PosDecision",
    "TOKEN_MAP",
    "UPOS_ALLOWLIST",
    "is_upos",
    "normalize_legacy_pos",
]

# ---------------------------------------------------------------------------
# Vocabulary contracts
# ---------------------------------------------------------------------------

#: The 17 Universal POS tags this project is allowed to emit.
UPOS_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ADJ",
        "ADP",
        "ADV",
        "AUX",
        "CCONJ",
        "DET",
        "INTJ",
        "NOUN",
        "NUM",
        "PART",
        "PRON",
        "PROPN",
        "PUNCT",
        "SCONJ",
        "SYM",
        "VERB",
        "X",
    }
)

#: How a value ended up in ``pos_canonical`` / ``pos_candidates``.
EVIDENCE_VALUES: frozenset[str] = frozenset(
    {
        "documented",
        "corpus-observed",
        "expert-validated",
        "inferred",
        "experimental",
        "unknown",
    }
)

#: Confidence attached to every emitted candidate (legacy label ⇒ low certainty).
CANDIDATE_CONFIDENCE = 0.5

# Labels are split on ``&``, ``,`` and ``/`` before token lookup.
_SEPARATOR_RE = re.compile(r"[&,/]+")

# ---------------------------------------------------------------------------
# Legacy token → UPOS map
#
# Keys are lowercase (callers casefold first).  A value is either a single
# UPOS tag (unambiguous) or a tuple of candidate tags (ambiguous label).
# Anything absent from this map normalizes to ``evidence="unknown"`` — that
# deliberately covers affix markers (``prefix``, ``suffix``), junk
# (``pt par``, ``dead``, ``pas. part.``) and placeholders (``unknown``, ``vs``).
# ---------------------------------------------------------------------------
TOKEN_MAP: dict[str, str | tuple[str, ...]] = {
    # -- dictionary / vocabulary long forms (22 legacy values) -------------
    "noun": "NOUN",
    "verb": "VERB",
    "transitive verb": "VERB",
    "intransitive verb": "VERB",
    "phrasal verb": "VERB",
    "adjective": "ADJ",
    "adverb": "ADV",
    "pronoun": "PRON",
    "preposition": "ADP",
    "particle": "PART",
    "interjection": "INTJ",
    "intj": "INTJ",
    "interj": "INTJ",
    "exclamation": "INTJ",
    "conjunction": ("CCONJ", "SCONJ"),
    "poss": ("PRON", "DET"),
    # -- zolai_vocabulary abbreviations ------------------------------------
    "n": "NOUN",
    "v": "VERB",
    "vt": "VERB",
    "vi": "VERB",
    "a": "ADJ",
    "adj": "ADJ",
    "adv": "ADV",
    "prep": "ADP",
    "pron": "PRON",
    "pro": "PRON",
    "num": "NUM",
    "punct": "PUNCT",
    "det": "DET",
    "part": "PART",
    "conj": ("CCONJ", "SCONJ"),
    # -- canonical tags that also appear verbatim in the data --------------
    "propn": "PROPN",
    "aux": "AUX",
    "sconj": "SCONJ",
    "ccconj": "CCONJ",
    "sym": "SYM",
    "x": "X",
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PosDecision:
    """Outcome of normalizing one legacy ``pos`` string.

    Attributes:
        raw: The original input (unchanged — never normalized in place).
        canonical: A tag from :data:`UPOS_ALLOWLIST`, or ``None`` when the
            label is ambiguous / unmapped / empty.
        candidates: Ordered candidate UPOS tags when ``canonical is None``
            and the label is interpretable (else empty).
        evidence: One of :data:`EVIDENCE_VALUES`.
    """

    raw: str
    canonical: str | None
    candidates: tuple[str, ...]
    evidence: str

    def __post_init__(self) -> None:
        if self.canonical is not None and self.canonical not in UPOS_ALLOWLIST:
            raise ValueError(f"canonical {self.canonical!r} is not a 17-tag UPOS")
        for tag in self.candidates:
            if tag not in UPOS_ALLOWLIST:
                raise ValueError(f"candidate {tag!r} is not a 17-tag UPOS")
        if self.evidence not in EVIDENCE_VALUES:
            raise ValueError(f"unknown evidence value: {self.evidence!r}")

    @property
    def untouched(self) -> bool:
        """True when the input was empty/whitespace — nothing to normalize."""
        return not self.raw.strip()

    @property
    def candidate_dicts(self) -> list[dict[str, float]]:
        """Candidates rendered as ``[{"upos": ..., "confidence": 0.5}, ...]``."""
        return [
            {"upos": tag, "confidence": CANDIDATE_CONFIDENCE}
            for tag in self.candidates
        ]

    @property
    def candidates_json(self) -> str:
        """Candidates as the JSON payload stored in ``pos_candidates``."""
        return json.dumps(self.candidate_dicts, ensure_ascii=False)


def is_upos(value: str) -> bool:
    """Return True when ``value`` is one of the 17 allowed UPOS tags."""
    return value in UPOS_ALLOWLIST


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_legacy_pos(raw: str | None) -> PosDecision:
    """Normalize a legacy ``pos`` label into a :class:`PosDecision`.

    Rules (applied in order):

    1. Empty / whitespace-only input → untouched: ``canonical=None``,
       ``evidence="unknown"`` (the caller skips such rows entirely).
    2. Trim + casefold, then split on ``&``, ``,`` and ``/``.
    3. Every part must resolve through :data:`TOKEN_MAP`; any unmapped part
       (affix marker, junk, placeholder) → ``canonical=None``,
       ``candidates=()``, ``evidence="unknown"``.
    4. Parts collapsing to a single UPOS → that UPOS,
       ``evidence="inferred"`` (e.g. ``adv & a`` is *not* such a case, but
       ``V`` alone is).
    5. Two or more distinct UPOS tags → ``canonical=None`` with the ordered
       candidate list, ``evidence="inferred"``.

    Args:
        raw: Raw ``pos`` value from the database (may be ``None``).

    Returns:
        A :class:`PosDecision` whose ``canonical`` is always ``None`` or a
        member of :data:`UPOS_ALLOWLIST`.
    """
    text = "" if raw is None else str(raw)
    if not text.strip():
        return PosDecision(raw=text, canonical=None, candidates=(), evidence="unknown")

    folded = text.strip().casefold()
    parts = [p for p in (seg.strip() for seg in _SEPARATOR_RE.split(folded)) if p]
    if not parts:
        return PosDecision(raw=text, canonical=None, candidates=(), evidence="unknown")

    resolved: list[str | tuple[str, ...]] = []
    for part in parts:
        mapped = TOKEN_MAP.get(part)
        if mapped is None:
            return PosDecision(raw=text, canonical=None, candidates=(), evidence="unknown")
        resolved.append(mapped)

    ordered: list[str] = []
    for item in resolved:
        tags = (item,) if isinstance(item, str) else item
        for tag in tags:
            if tag not in ordered:
                ordered.append(tag)

    if len(ordered) == 1:
        return PosDecision(raw=text, canonical=ordered[0], candidates=(), evidence="inferred")
    return PosDecision(raw=text, canonical=None, candidates=tuple(ordered), evidence="inferred")
