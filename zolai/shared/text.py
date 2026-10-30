"""Canonical word tokenizer — the single word-segmentation entry point.

Master Prompt §36 Phase 2, capability 1 (tokenization).  Several modules
historically re-declared the same word regex (attestation loaders, the POS
tagger); this module is the one place that owns it.

Semantics follow the attestation loaders' original inline
``re.findall(..., text.lower())`` calls, with one correction: the character
class covers the whole Latin-1 Supplement (U+00C0–U+00FF) as well as Latin
Extended-A/B.  The old class started at U+0100, so accented Zolai letters
(``à``, ``ê``, ``ô``, ``ì``, ``ù``) were not word characters and every
accented word was silently dropped — 17.5k Bible verses and 10k articles
carry them.

- pattern ``\\b[a-zA-Z\\u00C0-\\u024F'-]+\\b`` (apostrophe and hyphen inside
  the token);
- leading/trailing ``-'`` are stripped afterwards, because the character
  class makes them word characters for regex purposes: ``TDM-2010`` split as
  ``tdm-`` (the trailing hyphen is a boundary, not part of the word);
- lowercase the input before matching;
- no filtering here — callers keep their own ``len(word) >= 2`` policy.

Cross-domain imports go through ``shared`` (ownership rule): subword/subtoken
consolidation is deferred to Phase 3 (single-tokenizer D3), so this stays a
pure word-level helper with no DB, no network and no I/O.
"""

from __future__ import annotations

import re

__all__ = ["WORD_RE", "tokenize_words"]

#: The canonical word pattern (regex source string — compiled on use by ``re``).
#: Kept as the exact source string the attestation loaders matched inline.
WORD_RE = r"\b[a-zA-Z\u00c0-\u024f'-]+\b"

_WORD_RE = re.compile(WORD_RE)


def tokenize_words(text: str) -> list[str]:
    """Split ``text`` into lowercase word tokens.

    Args:
        text: Raw text of any language (Zolai, English, mixed).

    Returns:
        Lowercased word tokens in order of appearance. Empty input → ``[]``.
    """
    if not text:
        return []
    tokens = (token.strip("-'") for token in _WORD_RE.findall(text.lower()))
    return [token for token in tokens if token]
