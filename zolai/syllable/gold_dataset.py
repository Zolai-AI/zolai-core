"""Gold dataset format and annotation tools for syllable segmentation.

CoNLL-style annotation where characters are column-aligned with BIO tags.
Supports parsing, writing, and inter-annotator agreement (Cohen's kappa).

Annotation format::

    p   a   s   i   a   n
    B   I   I   B   I   I
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

# --- BIO encoding/decoding ---------------------------------------------------

def word_to_bio(
    word: str, syllables: list[str]
) -> list[str]:
    """Convert syllable list to BIO tags for a word.

    Args:
        word: The original word.
        syllables: List of syllable strings.

    Returns:
        List of BIO tags, one per character.

    Raises:
        ValueError: If syllables don't reconstruct the word.
    """
    reconstructed = "".join(syllables)
    if reconstructed != word:
        raise ValueError(
            f"Syllables {syllables!r} != word {word!r}"
        )
    tags: list[str] = []
    for i, syl in enumerate(syllables):
        for j, _ch in enumerate(syl):
            tags.append("B" if j == 0 else "I")
    return tags


def bio_to_word(word: str, tags: list[str]) -> list[str]:
    """Convert BIO tags back to syllable list.

    Args:
        word: The original word.
        tags: List of BIO tags, one per character.

    Returns:
        List of syllable strings.
    """
    syllables: list[str] = []
    current: list[str] = []
    for ch, tag in zip(word, tags):
        if tag == "B":
            if current:
                syllables.append("".join(current))
            current = [ch]
        else:
            current.append(ch)
    if current:
        syllables.append("".join(current))
    return syllables if syllables else [word]


# --- Feature extraction for CRF --------------------------------------------

def extract_features(word: str) -> list[dict[str, str]]:
    """Extract character-level features for CRF training.

    For each character position, returns a feature dict with
    unigram, bigram, trigram, orthographic, and phonotactic features.

    Args:
        word: A single Zolai word (lowercase).

    Returns:
        List of feature dicts, one per character position.
    """
    from .rules import (
        DIGRAPHS,
        DIPHTHONGS,
        VALID_CODAS,
        VOWELS,
    )

    features: list[dict[str, str]] = []
    word_lower = word.lower()

    for i in range(len(word)):
        feat: dict[str, str] = {}

        # Unigram
        feat["ch"] = word_lower[i]
        feat["ch_prev"] = (
            word_lower[i - 1] if i > 0 else "<START>"
        )
        feat["ch_next"] = (
            word_lower[i + 1] if i < len(word) - 1 else "<END>"
        )

        # Bigram
        feat["bigram_prev"] = (
            word_lower[i - 1:i + 1] if i > 0 else "<START>"
        )
        feat["bigram_next"] = (
            word_lower[i:i + 2]
            if i < len(word) - 1
            else "<END>"
        )

        # Trigram
        feat["trigram_prev"] = (
            word_lower[i - 2:i + 1] if i >= 2
            else word_lower[:i + 1]
        )

        # Orthographic
        feat["is_upper"] = str(word[i].isupper())
        feat["is_digit"] = str(word[i].isdigit())

        # Phonotactic
        ch = word_lower[i]
        feat["is_vowel"] = str(ch in VOWELS)
        feat["is_consonant"] = str(ch.isalpha() and ch not in VOWELS)
        feat["is_digraph_start"] = str(
            word_lower[i:i + 2] in DIGRAPHS
        )
        feat["is_diphthong_start"] = str(
            word_lower[i:i + 2] in DIPHTHONGS
        )
        feat["is_valid_coda"] = str(ch in VALID_CODAS)

        # Position
        feat["word_start"] = str(i == 0)
        feat["word_end"] = str(i == len(word) - 1)
        feat["dist_from_start"] = str(i)
        feat["dist_from_end"] = str(len(word) - 1 - i)

        features.append(feat)

    return features


# --- CoNLL file I/O ---------------------------------------------------------

def parse_conll(text: str) -> list[tuple[str, list[str]]]:
    """Parse CoNLL-style annotation text.

    Blocks are separated by blank lines. Each block has a line
    of characters and a line of BIO tags.

    Args:
        text: CoNLL-formatted annotation text.

    Returns:
        List of (word, syllables) pairs.
    """
    results: list[tuple[str, list[str]]] = []
    blocks = text.strip().split("\n\n")

    for block in blocks:
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        chars = lines[0].split()
        tags = lines[1].split()
        if len(chars) != len(tags):
            continue
        word = "".join(chars)
        syllables = bio_to_word(word, tags)
        results.append((word, syllables))

    return results


def write_conll(
    data: list[tuple[str, list[str]]],
    file: TextIO | Path | str | None = None,
) -> str:
    """Write CoNLL-style annotation text.

    Args:
        data: List of (word, syllables) pairs.
        file: Optional file path or file object to write to.

    Returns:
        The formatted CoNLL string.
    """
    blocks: list[str] = []
    for word, syllables in data:
        chars = list(word)
        tags = word_to_bio(word, syllables)
        char_line = " ".join(chars)
        tag_line = " ".join(tags)
        blocks.append(f"{char_line}\n{tag_line}")

    text = "\n\n".join(blocks) + "\n"

    if file is not None:
        if isinstance(file, (Path, str)):
            Path(file).write_text(text, encoding="utf-8")
        else:
            file.write(text)

    return text


# --- Inter-annotator agreement ----------------------------------------------

def cohens_kappa(
    annotator_a: list[list[str]],
    annotator_b: list[list[str]],
) -> float:
    """Compute Cohen's kappa for two sets of BIO tag sequences.

    Args:
        annotator_a: List of tag sequences from annotator A.
        annotator_b: List of tag sequences from annotator B.

    Returns:
        Cohen's kappa score (-1 to 1).
    """
    if len(annotator_a) != len(annotator_b):
        raise ValueError("Annotators must have same number of items")

    total = 0
    agree = 0
    class_a: dict[str, int] = {}
    class_b: dict[str, int] = {}

    for tags_a, tags_b in zip(annotator_a, annotator_b):
        if len(tags_a) != len(tags_b):
            continue
        for t_a, t_b in zip(tags_a, tags_b):
            total += 1
            if t_a == t_b:
                agree += 1
            class_a[t_a] = class_a.get(t_a, 0) + 1
            class_b[t_b] = class_b.get(t_b, 0) + 1

    if total == 0:
        return 0.0

    p_o = agree / total  # observed agreement
    p_e = sum(
        (class_a.get(k, 0) / total) * (class_b.get(k, 0) / total)
        for k in set(class_a) | set(class_b)
    )

    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)
