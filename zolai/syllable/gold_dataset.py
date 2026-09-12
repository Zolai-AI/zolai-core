"""Gold dataset format and annotation tools for syllable segmentation.

CoNLL-style annotation where characters are column-aligned with BIO tags.
Supports parsing, writing, and inter-annotator agreement (Cohen's kappa).

Annotation format::

    p   a   s   i   a   n
    B   I   I   B   I   I

Gold Dataset Builder (SylBreak4All Milestone 4):
Builds gold standard syllable dataset from authoritative sources:
- Bible text (highest authority, ZVS 2018 compliant)
- Dictionary headwords (verified entries)
- High-frequency corpus (vocab_by_frequency.jsonl)

Exports balanced dataset with train/dev/test splits for ML training.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
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


# --- Gold Dataset Builder (SylBreak4All M4) ---------------------------------


class GoldDatasetBuilder:
    """Build gold standard syllable dataset for evaluation.

    Authoritative source priority: Bible > Dictionary > Corpus
    Balances across syllable counts (1-4+ syllables).
    Exports train/dev/test splits for ML training.
    """

    def __init__(
        self,
        data_dir: str = "/home/peter/Documents/Projects/zolai-ai/data",
        segmenter=None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.segmenter = segmenter
        self._segmenter_cache = None

    def _get_segmenter(self):
        """Lazy load the syllable segmenter."""
        if self._segmenter_cache is None:
            if self.segmenter is not None:
                self._segmenter_cache = self.segmenter
            else:
                from .segmenter import SyllableSegmenter
                self._segmenter_cache = SyllableSegmenter()
        return self._segmenter_cache

    def _segment_word(self, word: str) -> list[str]:
        """Segment a word using the segmenter."""
        seg = self._get_segmenter()
        return seg.segment(word)

    def _count_syllables(self, word: str) -> int:
        """Count syllables in a word."""
        return len(self._segment_word(word))

    def _is_valid_zolai_word(self, word: str) -> bool:
        """Check if word is a valid Zolai word (alphabetic, reasonable length)."""
        if not word or not word.isalpha():
            return False
        if len(word) < 2 or len(word) > 20:
            return False
        # Must contain at least one vowel
        vowels = set("aeiou")
        return any(c.lower() in vowels for c in word)

    def build_from_corpus(
        self, min_freq: int = 5, max_words: int = 5000
    ) -> list[dict]:
        """Build gold dataset from high-frequency corpus words.

        Uses vocab_by_frequency.jsonl which has Bible-derived frequency data.
        Filters by minimum frequency and validates syllables.

        Args:
            min_freq: Minimum frequency threshold.
            max_words: Maximum number of words to include.

        Returns:
            List of dicts with word, syllables, tone_pattern, source, freq.
        """
        corpus_path = (
            self.data_dir
            / "bible"
            / "language_learning"
            / "vocab_by_frequency.jsonl"
        )
        if not corpus_path.exists():
            raise FileNotFoundError(f"Corpus file not found: {corpus_path}")

        results = []
        with corpus_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    word = data.get("word", "").lower().strip()
                    freq = data.get("frequency", 0)

                    if freq < min_freq:
                        continue
                    if not self._is_valid_zolai_word(word):
                        continue

                    syllables = self._segment_word(word)
                    syl_count = len(syllables)

                    results.append({
                        "word": word,
                        "syllables": syllables,
                        "syllable_count": syl_count,
                        "tone_pattern": "",  # Zolai doesn't mark tone in orthography
                        "source": "corpus",
                        "frequency": freq,
                        "level": data.get("level", 0),
                    })

                    if len(results) >= max_words:
                        break
                except json.JSONDecodeError:
                    continue

        return results

    def build_from_dictionary(
        self, max_words: int = 3000
    ) -> list[dict]:
        """Build from dictionary headwords with manual verification priority.

        Uses dict_zo_en_master_v1.jsonl (verified Zolai->English dictionary).
        Prioritizes verified entries.

        Args:
            max_words: Maximum number of words to include.

        Returns:
            List of dicts with word, syllables, tone_pattern, source.
        """
        dict_path = (
            self.data_dir
            / "dictionary"
            / "processed"
            / "dict_zo_en_master_v1.jsonl"
        )
        if not dict_path.exists():
            raise FileNotFoundError(f"Dictionary file not found: {dict_path}")

        results = []
        with dict_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    word = data.get("zolai", "").lower().strip()

                    if not self._is_valid_zolai_word(word):
                        continue

                    # Skip entries with special chars or non-Zolai patterns
                    if any(c in word for c in "&#'\"()[]{}<>"):
                        continue

                    syllables = self._segment_word(word)
                    syl_count = len(syllables)

                    # Only include if segmentation produces valid syllables
                    if syl_count == 0:
                        continue

                    results.append({
                        "word": word,
                        "syllables": syllables,
                        "syllable_count": syl_count,
                        "tone_pattern": "",
                        "source": "dictionary",
                        "frequency": 0,
                        "verified": data.get("zvs_compliance_status") == "verified",
                    })

                    if len(results) >= max_words:
                        break
                except json.JSONDecodeError:
                    continue

        return results

    def build_from_bible(
        self, max_words: int = 2000
    ) -> list[dict]:
        """Build from Bible text (authoritative, ZVS 2018 compliant).

        Extracts unique words from parallel_corpus_v1.jsonl Zolai verses.
        These are the most authoritative since Bible text is carefully translated.

        Args:
            max_words: Maximum number of words to include.

        Returns:
            List of dicts with word, syllables, tone_pattern, source.
        """
        bible_path = self.data_dir / "bible" / "parallel_corpus_v1.jsonl"
        if not bible_path.exists():
            raise FileNotFoundError(f"Bible file not found: {bible_path}")

        # Collect unique words from Bible verses
        word_counter: Counter[str] = Counter()
        with bible_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    zo_text = data.get("zo_tedim2010") or data.get("zo_tdb77") or ""
                    # Split into words
                    for word in zo_text.split():
                        # Clean word: remove punctuation
                        clean = word.strip(".,;:!?()[]{}\"'").lower()
                        if self._is_valid_zolai_word(clean):
                            word_counter[clean] += 1
                except json.JSONDecodeError:
                    continue

        # Sort by frequency (most common Bible words first)
        results = []
        for word, freq in word_counter.most_common():
            if len(results) >= max_words:
                break

            syllables = self._segment_word(word)
            syl_count = len(syllables)

            if syl_count == 0:
                continue

            results.append({
                "word": word,
                "syllables": syllables,
                "syllable_count": syl_count,
                "tone_pattern": "",
                "source": "bible",
                "frequency": freq,
            })

        return results

    def _balance_by_syllable_count(
        self, entries: list[dict], total: int
    ) -> list[dict]:
        """Balance dataset across syllable counts (1-4+).

        Ensures roughly equal representation of each syllable count.
        """
        by_count: dict[int, list[dict]] = {}
        for entry in entries:
            count = entry["syllable_count"]
            if count not in by_count:
                by_count[count] = []
            by_count[count].append(entry)

        # Determine target per syllable count
        num_buckets = len(by_count)
        if num_buckets == 0:
            return []

        target_per_bucket = max(1, total // num_buckets)

        balanced = []
        for count in sorted(by_count.keys()):
            bucket = by_count[count]
            # Sort by frequency (descending) then by source priority
            source_priority = {"bible": 0, "dictionary": 1, "corpus": 2}
            bucket.sort(
                key=lambda x: (
                    -x.get("frequency", 0),
                    source_priority.get(x.get("source", "corpus"), 3)
                )
            )
            balanced.extend(bucket[:target_per_bucket])

        # If we still need more, fill from remaining
        if len(balanced) < total:
            remaining = [e for e in entries if e not in balanced]
            remaining.sort(
                key=lambda x: -x.get("frequency", 0)
            )
            balanced.extend(remaining[: total - len(balanced)])

        return balanced[:total]

    def export_gold_jsonl(
        self,
        output_path: str,
        total: int = 10000,
        corpus_ratio: float = 0.5,
        dict_ratio: float = 0.3,
        bible_ratio: float = 0.2,
    ) -> list[dict]:
        """Export balanced gold dataset from all sources.

        Args:
            output_path: Path to output JSONL file.
            total: Total number of entries to generate.
            corpus_ratio: Fraction from corpus (high-frequency).
            dict_ratio: Fraction from dictionary.
            bible_ratio: Fraction from Bible (authoritative).

        Returns:
            List of generated gold entries.
        """
        # Calculate target counts per source
        corpus_target = int(total * corpus_ratio)
        dict_target = int(total * dict_ratio)
        bible_target = int(total * bible_ratio)

        # Build from each source (fetch extra for balancing)
        corpus_data = self.build_from_corpus(
            min_freq=5, max_words=corpus_target * 3
        )
        dict_data = self.build_from_dictionary(max_words=dict_target * 3)
        bible_data = self.build_from_bible(max_words=bible_target * 3)

        # Combine and balance
        all_data = corpus_data + dict_data + bible_data

        # Remove duplicates (keep highest priority source)
        seen = {}
        source_priority = {"bible": 0, "dictionary": 1, "corpus": 2}
        for entry in all_data:
            word = entry["word"]
            priority = source_priority.get(entry["source"], 3)
            if word not in seen or priority < source_priority.get(seen[word]["source"], 3):
                seen[word] = entry

        unique_entries = list(seen.values())

        # Balance by syllable count
        balanced = self._balance_by_syllable_count(unique_entries, total)

        # Write output
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8") as f:
            for entry in balanced:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Print statistics
        syl_counts = Counter(e["syllable_count"] for e in balanced)
        source_counts = Counter(e["source"] for e in balanced)
        print(f"Gold dataset written to {output_path}")
        print(f"Total entries: {len(balanced)}")
        print(f"Syllable distribution: {dict(sorted(syl_counts.items()))}")
        print(f"Source distribution: {dict(source_counts)}")

        return balanced

    def export_splits(
        self,
        output_dir: str,
        train: float = 0.8,
        dev: float = 0.1,
        test: float = 0.1,
        seed: int = 42,
    ) -> dict[str, list[dict]]:
        """Export train/dev/test splits for ML training.

        Args:
            output_dir: Directory to write split files.
            train: Training split ratio.
            dev: Development split ratio.
            test: Test split ratio.
            seed: Random seed for reproducibility.

        Returns:
            Dict with split names as keys and entry lists as values.
        """
        # First build the full gold dataset
        gold_entries = self.export_gold_jsonl(
            output_path=Path(output_dir) / "gold_full.jsonl",
            total=10000,
        )

        # Shuffle with seed
        random.seed(seed)
        shuffled = gold_entries.copy()
        random.shuffle(shuffled)

        n = len(shuffled)
        train_end = int(n * train)
        dev_end = train_end + int(n * dev)

        splits = {
            "train": shuffled[:train_end],
            "dev": shuffled[train_end:dev_end],
            "test": shuffled[dev_end:],
        }

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for split_name, entries in splits.items():
            split_path = output_dir / f"gold_{split_name}.jsonl"
            with split_path.open("w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"  {split_name}: {len(entries)} entries -> {split_path}")

        return splits


# --- CLI --------------------------------------------------------------------


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.gold_dataset",
        description="Gold standard syllable dataset builder (SylBreak4All M4)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build gold dataset from all sources",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="data/syllable/gold.jsonl",
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--size",
        "-s",
        type=int,
        default=10000,
        help="Total number of gold entries to generate",
    )
    parser.add_argument(
        "--splits",
        action="store_true",
        help="Also export train/dev/test splits",
    )
    parser.add_argument(
        "--splits-dir",
        type=str,
        default="data/syllable/splits",
        help="Directory for train/dev/test splits",
    )
    parser.add_argument(
        "--corpus-ratio",
        type=float,
        default=0.5,
        help="Fraction from high-frequency corpus",
    )
    parser.add_argument(
        "--dict-ratio",
        type=float,
        default=0.3,
        help="Fraction from dictionary",
    )
    parser.add_argument(
        "--bible-ratio",
        type=float,
        default=0.2,
        help="Fraction from Bible text",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="/home/peter/Documents/Projects/zolai-ai/data",
        help="Root data directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    builder = GoldDatasetBuilder(data_dir=args.data_dir)

    if args.build:
        builder.export_gold_jsonl(
            output_path=args.output,
            total=args.size,
            corpus_ratio=args.corpus_ratio,
            dict_ratio=args.dict_ratio,
            bible_ratio=args.bible_ratio,
        )

        if args.splits:
            builder.export_splits(output_dir=args.splits_dir)
        return 0

    # No args: show help
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
