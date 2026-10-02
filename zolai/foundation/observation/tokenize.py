"""Capability 1 — sentence-level word tokenization for observations.

Thin wrapper over the canonical tokenizer in :mod:`zolai.shared.text`; the
observation pipeline owns the *policy* (minimum token length, empty-sentence
skip) while the pattern itself stays single-sourced (D3 — no duplicate layer).
"""

from __future__ import annotations

from ...shared.text import WORD_RE, tokenize_words

__all__ = ["MIN_TOKEN_LEN", "WORD_RE", "tokenize_sentence"]

#: Observation-level policy: keep every word token, including one-letter
#: agreement particles (``a``) — unlike attestation, which demands 2+ letters.
MIN_TOKEN_LEN = 1


def tokenize_sentence(text: str, *, min_len: int = MIN_TOKEN_LEN) -> list[str]:
    """Tokenize one source sentence into observation word tokens.

    Args:
        text: Raw sentence text exactly as stored in the source table.
        min_len: Drop tokens shorter than this (default keeps all words).

    Returns:
        Lowercased word tokens in order of appearance.
    """
    if not text:
        return []
    return [tok for tok in tokenize_words(text) if len(tok) >= min_len]
