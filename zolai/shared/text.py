"""Canonical word tokenizer — the single word-segmentation entry point.

Master Prompt §36 Phase 2, capability 1 (tokenization).  Several modules
historically re-declared the same word regex (attestation loaders, the POS
tagger); this module is the one place that owns it.

Semantics are pinned to the attestation loaders — ``tests/test_data_connections``
asserts the exact word sets they produce, so :func:`tokenize_words` is
byte-identical to the previous inline ``re.findall(..., text.lower())`` calls:

- pattern ``\\b[a-zA-Z\\u0100-\\u024F'-]+\\b`` (ASCII + Latin-1 supplement/ext,
  apostrophe and hyphen inside the token);
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
WORD_RE = r"\b[a-zA-Z\u0100-\u024F'-]+\b"

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
    return _WORD_RE.findall(text.lower())
