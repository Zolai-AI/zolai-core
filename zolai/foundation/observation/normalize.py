"""Capability 2 — token-level normalization (casefold → ZVS → cleaner fallback).

Normalization never mutates the source: the raw surface form stays on the
observation row and only the *derived* ``normalized_form`` key carries the
canonical spelling.

Pipeline (plan §2 / capability table row 2):

1. ``str.casefold()`` — canonical case handling;
2. the canonical ZVS map imported from :mod:`zolai.zvs.rules_data` — **no
   forbidden-form literals live here** (ZVS ownership stays in ``zolai/zvs``);
3. a ``corpus_clean.clean_value(text, "word")`` fallback for surfaces the
   canonical regex cannot vouch for (stray HTML/whitespace/exotica).

``zvs_corrected`` reports whether step 2 (or the ZVS step inside the fallback)
actually rewrote a token, so the pipeline can flag the observation in metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from ...zvs.rules_data import (
    COMPOUND_SPLIT_TO_PREFERRED,
    DIALECT_FORBIDDEN_TO_PREFERRED,
    STEM_FORBIDDEN_TO_PREFERRED,
)

__all__ = [
    "ZVS_TOKEN_MAP",
    "normalize_token",
    "normalize_tokens",
]

#: Canonical ZVS token map — the union of the three ``rules_data`` tables.
#: Compiled from the owned mappings, never re-typed (the compound table is
#: multi-word so it cannot match a single token; it is kept for completeness
#: and for callers that normalize spans).
ZVS_TOKEN_MAP: Mapping[str, str] = {
    **DIALECT_FORBIDDEN_TO_PREFERRED,
    **COMPOUND_SPLIT_TO_PREFERRED,
    **STEM_FORBIDDEN_TO_PREFERRED,
}

#: A surface that already matches the canonical word shape needs no fallback.
_CANONICAL_RE = re.compile(r"^[a-z][a-z'-]*$")


def _fallback(token: str) -> tuple[str, bool]:
    """Delegate an atypical surface to the corpus cleaner (lazy import)."""
    try:
        from ...data.corpus_clean import clean_value

        outcome = clean_value(token, "word")
    except Exception:  # noqa: BLE001 — normalization must never fail a build
        return token, False
    # ``cleaned`` already reverts blocked changes, so it is always safe to use.
    return outcome.cleaned or token, bool(outcome.zvs_applied)


def _normalize_one(token: str) -> tuple[str, bool]:
    if not token:
        return "", False
    lowered = token.casefold()
    mapped = ZVS_TOKEN_MAP.get(lowered)
    if mapped is not None:
        return mapped, mapped != lowered
    if _CANONICAL_RE.match(lowered):
        return lowered, False
    return _fallback(lowered)


def normalize_token(token: str) -> str:
    """Return the canonical form of a single word token."""
    return _normalize_one(token)[0]


def normalize_tokens(tokens: Iterable[str]) -> tuple[list[str], bool]:
    """Normalize a token sequence.

    Returns:
        ``(normalized_forms, zvs_corrected)`` where ``zvs_corrected`` is True
        when at least one token was rewritten by the ZVS map/fallback.
    """
    out: list[str] = []
    corrected = False
    for token in tokens:
        normalized, was_corrected = _normalize_one(token)
        out.append(normalized)
        corrected = corrected or was_corrected
    return out, corrected
