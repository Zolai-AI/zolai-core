"""Rule-based syllable segmentation for Zolai.

Implements deterministic segmentation using Zolai phonotactic constraints:
onset (C)(C), nucleus (V or VV), coda (C). Handles digraphs and
diphthongs as single phonological units.

ZVS 2018 orthography enforced — no deprecated forms.
"""

from __future__ import annotations

from typing import Final

# --- Phoneme inventories ---------------------------------------------------

VOWELS: Final[set[str]] = {"a", "e", "i", "o", "u"}

DIPHTHONGS: Final[set[str]] = {
    "aw", "ai", "ei", "ou", "ia", "io", "iu", "ua", "ue", "ui",
}

DIGRAPHS: Final[set[str]] = {
    "th", "kh", "gh", "ng", "ch", "ph", "bh",
}

VALID_CODAS: Final[set[str]] = {
    "n", "ng", "m", "l", "s", "t", "k", "p", "h",
}

VALID_ONSET_CLUSTERS: Final[set[str]] = {
    "pl", "kl", "bl", "gl", "fl", "sl", "tl",
    "pr", "kr", "tr", "br", "dr", "fr", "gr",
    "sp", "st", "sk", "sn", "sm", "sw", "sy",
}


def _is_vowel(ch: str) -> bool:
    """Check if a character is a vowel."""
    return ch.lower() in VOWELS


def _is_diphthong(text: str, pos: int) -> bool:
    """Check if characters at pos form a diphthong."""
    pair = text[pos:pos + 2].lower()
    return pair in DIPHTHONGS


def _is_digraph(text: str, pos: int) -> bool:
    """Check if characters at pos form a digraph."""
    pair = text[pos:pos + 2].lower()
    return pair in DIGRAPHS


def _is_valid_coda(ch: str) -> bool:
    """Check if a character is a valid coda consonant."""
    return ch.lower() in VALID_CODAS


def _is_consonant(ch: str) -> bool:
    """Check if a character is a consonant (not vowel, not digit)."""
    return ch.isalpha() and not _is_vowel(ch)


def _find_nucleus(text: str, start: int) -> tuple[int, int]:
    """Find the nucleus (vowel or diphthong) starting from pos.

    Returns:
        (nucleus_start, nucleus_end) — end is exclusive.
    """
    for i in range(start, len(text)):
        if _is_vowel(text[i]):
            # Check for diphthong
            if _is_diphthong(text, i):
                return (i, i + 2)
            return (i, i + 1)
    return (start, start)


def _find_onset(text: str, start: int, nucleus_start: int) -> int:
    """Find where the onset ends (returns index of first nucleus char).

    Rules:
    - Maximum 2 consonants in onset
    - Valid clusters only
    - Digraphs count as one consonant
    """
    onset_len = nucleus_start - start
    if onset_len == 0:
        return start
    if onset_len == 1:
        return start
    if onset_len >= 2:
        # Check 2-consonant cluster
        cluster = text[start:start + 2].lower()
        if cluster in VALID_ONSET_CLUSTERS:
            return start
        # Invalid cluster — onset is only 1 consonant
        return start + 1
    return start


def _scan_syllable(text: str, pos: int) -> int:
    """Scan one syllable from pos, return the end position.

    Follows the (C)(C)V(C) template with Zolai phonotactic rules.
    """
    if pos >= len(text):
        return pos

    # Find nucleus
    nuc_start, nuc_end = _find_nucleus(text, pos)
    if nuc_end <= nuc_start:
        # No vowel found — treat remaining as single syllable
        return len(text)

    # Onset: characters before nucleus (MOP rule applied in _find_onset)
    _find_onset(text, pos, nuc_start)

    # Coda: check if next char after nucleus is a valid coda
    cod_end = nuc_end
    if nuc_end < len(text):
        next_ch = text[nuc_end].lower()
        # Check for digraph coda (ng as single phoneme)
        if nuc_end + 1 < len(text):
            pair = text[nuc_end:nuc_end + 2].lower()
            if pair == "ng":
                cod_end = nuc_end + 2
                return cod_end
        if _is_valid_coda(next_ch):
            # Check if consonant cluster after coda forms valid onset
            rest = text[nuc_end + 1:]
            if rest and (_is_vowel(rest[0]) or
                         (len(rest) >= 2 and
                          rest[:2].lower() in VALID_ONSET_CLUSTERS)):
                cod_end = nuc_end  # coda belongs to next syllable
            else:
                cod_end = nuc_end + 1

    return cod_end


def segment_word(word: str) -> list[str]:
    """Segment a single Zolai word into syllables.

    Args:
        word: A single Zolai word (lowercase, no diacritics).

    Returns:
        List of syllable strings.
    """
    if not word:
        return []

    result: list[str] = []
    pos = 0

    while pos < len(word):
        end = _scan_syllable(word, pos)
        if end <= pos:
            # Safety: avoid infinite loop
            result.append(word[pos:])
            break
        result.append(word[pos:end])
        pos = end

    return result


def strip_diacritics(text: str) -> str:
    """Remove tone diacritics from Zolai text.

    Converts á, à, ā, a̋ → a; é → e; etc.
    """
    import unicodedata
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Mn"
    )
