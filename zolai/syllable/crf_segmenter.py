"""CRF-based syllable segmenter for Zolai (SylBreak4All Milestone 5).

Uses sklearn-crfsuite for sequence labeling with BIO tagging scheme:
B-SYL (begin), I-SYL (inside), E-SYL (end), S-SYL (single-char syllable).

Features: character n-grams, vowel/consonant patterns, digraph/diphthong
detection, position features, word length.

Falls back to rule-based segmentation for OOV words.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Final

try:
    import sklearn_crfsuite  # type: ignore
except ImportError:
    sklearn_crfsuite = None  # type: ignore

from .rules import (
    DIGRAPHS,
    DIPHTHONGS,
    VALID_CODAS,
    VALID_ONSET_CLUSTERS,
    VOWELS,
    strip_diacritics,
)

# --- BIO Tagging Scheme ---
# B-SYL: Begin syllable
# I-SYL: Inside syllable
# E-SYL: End syllable
# S-SYL: Single-character syllable

BIO_TAGS: Final[list[str]] = ["B-SYL", "I-SYL", "E-SYL", "S-SYL"]


def _is_vowel(ch: str) -> bool:
    """Check if a character is a vowel."""
    return ch.lower() in VOWELS


def _is_consonant(ch: str) -> bool:
    """Check if a character is a consonant."""
    return ch.isalpha() and not _is_vowel(ch)


def _is_digraph(text: str, pos: int) -> bool:
    """Check if characters at pos form a digraph."""
    if pos + 1 >= len(text):
        return False
    return text[pos:pos + 2].lower() in DIGRAPHS


def _is_diphthong(text: str, pos: int) -> bool:
    """Check if characters at pos form a diphthong."""
    if pos + 1 >= len(text):
        return False
    return text[pos:pos + 2].lower() in DIPHTHONGS


def _is_valid_coda(ch: str) -> bool:
    """Check if character is a valid coda."""
    return ch.lower() in VALID_CODAS


def _is_valid_onset_cluster(text: str, pos: int) -> bool:
    """Check if characters form a valid onset cluster."""
    if pos + 1 >= len(text):
        return False
    return text[pos:pos + 2].lower() in VALID_ONSET_CLUSTERS


class CRFSyllableSegmenter:
    """CRF-based syllable segmenter for Zolai.

    Uses character-level sequence labeling with BIO tagging.
    Trained on gold-standard syllable annotations.

    Example:
        >>> segmenter = CRFSyllableSegmenter()
        >>> segmenter.train("data/syllable/splits/gold_train.jsonl")
        >>> segmenter.predict("vantung")
        ['van', 'tung']
    """

    def __init__(self, model_path: str | None = None) -> None:
        """Initialize the CRF segmenter.

        Args:
            model_path: Optional path to a pre-trained model file.
        """
        if sklearn_crfsuite is None:
            raise ImportError(
                "sklearn-crfsuite is required for CRF segmenter. "
                "Install with: pip install sklearn-crfsuite"
            )

        self.model_path = model_path
        self.crf: sklearn_crfsuite.CRF | None = None
        self._rule_segmenter = None

        if model_path:
            self.load(model_path)

    def _get_rule_segmenter(self):
        """Lazy-load rule-based segmenter for fallback."""
        if self._rule_segmenter is None:
            from .segmenter import SyllableSegmenter
            self._rule_segmenter = SyllableSegmenter()
        return self._rule_segmenter

    def extract_features(self, word: str, position: int) -> dict[str, str]:
        """Extract features for character at position.

        Args:
            word: Input word (lowercase, no diacritics).
            position: Character index in word.

        Returns:
            Feature dictionary for CRF.
        """
        word_lower = word.lower()
        feat: dict[str, str] = {}

        # Current character
        feat["ch"] = word_lower[position]

        # Previous characters (unigram, bigram, trigram)
        feat["ch-1"] = word_lower[position - 1] if position > 0 else "<START>"
        feat["ch-2"] = word_lower[position - 2] if position > 1 else "<START>"

        # Next characters
        feat["ch+1"] = (
            word_lower[position + 1] if position < len(word) - 1 else "<END>"
        )
        feat["ch+2"] = (
            word_lower[position + 2] if position < len(word) - 2 else "<END>"
        )

        # Bigrams
        feat["bigram-1"] = (
            word_lower[position - 1:position + 1] if position > 0 else "<START>"
        )
        feat["bigram+1"] = (
            word_lower[position:position + 2]
            if position < len(word) - 1
            else "<END>"
        )

        # Trigrams
        if position >= 2:
            feat["trigram-2"] = word_lower[position - 2:position + 1]
        else:
            feat["trigram-2"] = word_lower[:position + 1]

        if position < len(word) - 2:
            feat["trigram+2"] = word_lower[position:position + 3]
        else:
            feat["trigram+2"] = word_lower[position:] + "<END>" * (3 - (len(word) - position))

        # Orthographic features
        feat["is_upper"] = str(word[position].isupper())
        feat["is_digit"] = str(word[position].isdigit())

        # Phonotactic features
        ch = word_lower[position]
        feat["is_vowel"] = str(ch in VOWELS)
        feat["is_consonant"] = str(_is_consonant(ch))
        feat["is_digraph_start"] = str(_is_digraph(word_lower, position))
        feat["is_digraph_end"] = str(
            position > 0 and _is_digraph(word_lower, position - 1)
        )
        feat["is_diphthong_start"] = str(_is_diphthong(word_lower, position))
        feat["is_diphthong_end"] = str(
            position > 0 and _is_diphthong(word_lower, position - 1)
        )
        feat["is_valid_coda"] = str(_is_valid_coda(ch))
        feat["is_valid_onset_cluster_start"] = str(_is_valid_onset_cluster(word_lower, position))
        feat["is_valid_onset_cluster_end"] = str(
            position > 0 and _is_valid_onset_cluster(word_lower, position - 1)
        )

        # Position features
        feat["pos"] = str(position)
        feat["word_len"] = str(len(word))
        feat["rel_pos"] = str(position / max(1, len(word) - 1))
        feat["dist_from_start"] = str(position)
        feat["dist_from_end"] = str(len(word) - 1 - position)
        feat["is_word_start"] = str(position == 0)
        feat["is_word_end"] = str(position == len(word) - 1)

        # Vowel/consonant pattern around current position
        # Pattern of 3 chars centered on current (C/V/_)
        pattern = []
        for offset in (-1, 0, 1):
            idx = position + offset
            if 0 <= idx < len(word):
                c = word_lower[idx]
                if c in VOWELS:
                    pattern.append("V")
                elif c.isalpha():
                    pattern.append("C")
                else:
                    pattern.append("_")
            else:
                pattern.append("_")
        feat["cv_pattern"] = "".join(pattern)

        # Pattern of 5 chars
        pattern5 = []
        for offset in (-2, -1, 0, 1, 2):
            idx = position + offset
            if 0 <= idx < len(word):
                c = word_lower[idx]
                if c in VOWELS:
                    pattern5.append("V")
                elif c.isalpha():
                    pattern5.append("C")
                else:
                    pattern5.append("_")
            else:
                pattern5.append("_")
        feat["cv_pattern5"] = "".join(pattern5)

        return feat

    def word_to_features(self, word: str) -> list[dict[str, str]]:
        """Convert word to feature sequence for CRF.

        Args:
            word: Input word.

        Returns:
            List of feature dicts, one per character.
        """
        normalized = strip_diacritics(word).lower().strip()
        if not normalized:
            return []

        return [self.extract_features(normalized, i) for i in range(len(normalized))]

    def word_to_labels(self, word: str, syllables: list[str]) -> list[str]:
        """Convert syllable list to BIO tags (B-SYL, I-SYL, E-SYL, S-SYL).

        Args:
            word: Original word.
            syllables: List of syllable strings.

        Returns:
            List of BIO tags, one per character.

        Raises:
            ValueError: If syllables don't reconstruct the word.
        """
        normalized = strip_diacritics(word).lower().strip()
        reconstructed = "".join(strip_diacritics(s).lower() for s in syllables)
        if reconstructed != normalized:
            raise ValueError(
                f"Syllables {syllables!r} don't reconstruct word {normalized!r}"
            )

        tags: list[str] = []
        for syl in syllables:
            syl_norm = strip_diacritics(syl).lower()
            syl_len = len(syl_norm)
            if syl_len == 1:
                tags.append("S-SYL")
            elif syl_len == 2:
                tags.extend(["B-SYL", "E-SYL"])
            else:
                tags.append("B-SYL")
                tags.extend(["I-SYL"] * (syl_len - 2))
                tags.append("E-SYL")

        return tags

    def _load_gold_jsonl(self, path: str | Path) -> list[tuple[str, list[str]]]:
        """Load gold data from JSONL file.

        Expected format per line: {"word": "...", "syllables": [...], ...}

        Args:
            path: Path to gold dataset JSONL file.

        Returns:
            List of (word, syllables) tuples.
        """
        gold_data: list[tuple[str, list[str]]] = []
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Gold dataset not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    word = data.get("word", "").strip()
                    syllables = data.get("syllables", [])
                    if word and syllables:
                        gold_data.append((word, syllables))
                except json.JSONDecodeError:
                    continue

        return gold_data

    def train(
        self,
        train_file: str | Path,
        dev_file: str | Path | None = None,
        algorithm: str = "lbfgs",
        c1: float = 0.1,
        c2: float = 0.1,
        max_iterations: int = 100,
        all_possible_transitions: bool = True,
    ) -> dict:
        """Train CRF on gold dataset.

        Args:
            train_file: Path to training data JSONL.
            dev_file: Optional path to development data for early stopping.
            algorithm: CRF algorithm ('lbfgs' or 'l2sgd').
            c1: L1 regularization coefficient.
            c2: L2 regularization coefficient.
            max_iterations: Maximum training iterations.
            all_possible_transitions: Allow all label transitions.

        Returns:
            Training statistics dict.
        """
        # Load training data
        train_data = self._load_gold_jsonl(train_file)
        if not train_data:
            raise ValueError(f"No training data loaded from {train_file}")

        words = [w for w, _ in train_data]
        tag_sequences = [self.word_to_labels(w, syls) for w, syls in train_data]
        features = [self.word_to_features(w) for w in words]

        print(f"Training on {len(words)} words...")
        print(f"  Average word length: {sum(len(w) for w in words) / len(words):.1f}")
        print(f"  Total characters: {sum(len(w) for w in words)}")

        # Initialize CRF
        self.crf = sklearn_crfsuite.CRF(
            algorithm=algorithm,
            c1=c1,
            c2=c2,
            max_iterations=max_iterations,
            all_possible_transitions=all_possible_transitions,
            verbose=True,
        )

        # Train
        self.crf.fit(features, tag_sequences)

        # Evaluate on dev set if provided
        dev_stats = {}
        if dev_file:
            dev_data = self._load_gold_jsonl(dev_file)
            if dev_data:
                dev_stats = self.evaluate(dev_data)
                print(f"Dev set results: {dev_stats}")

        # Save model path
        self.model_path = str(train_file).replace("gold_train.jsonl", "crf_syllable.pkl")

        return {
            "train_size": len(words),
            "total_chars": sum(len(w) for w in words),
            "dev_stats": dev_stats,
        }

    def predict(self, word: str) -> list[str]:
        """Predict syllable boundaries for a word.

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.

        Raises:
            RuntimeError: If model not loaded/trained.
        """
        if self.crf is None:
            # Fallback to rule-based
            return self._get_rule_segmenter().segment(word)

        normalized = strip_diacritics(word).lower().strip()
        if not normalized:
            return []

        features = self.word_to_features(normalized)
        tags = self.crf.predict_single(features)
        return self._tags_to_syllables(normalized, tags)

    def segment(self, word: str) -> list[str]:
        """Segment a word into syllables (alias for predict).

        Args:
            word: A single Zolai word.

        Returns:
            List of syllable strings.
        """
        return self.predict(word)

    def _tags_to_syllables(self, word: str, tags: list[str]) -> list[str]:
        """Convert BIO tags to syllable list."""
        syllables: list[str] = []
        current: list[str] = []

        for ch, tag in zip(word, tags):
            if tag in ("B-SYL", "S-SYL"):
                if current:
                    syllables.append("".join(current))
                current = [ch]
            else:  # I-SYL or E-SYL
                current.append(ch)

        if current:
            syllables.append("".join(current))

        return syllables if syllables else [word]

    def evaluate(self, test_file: str | Path | list[tuple[str, list[str]]]) -> dict:
        """Evaluate on test set.

        Args:
            test_file: Path to test data JSONL, or list of (word, syllables) tuples.

        Returns:
            Dictionary with evaluation metrics.
        """
        if isinstance(test_file, (str, Path)):
            test_data = self._load_gold_jsonl(test_file)
        else:
            test_data = test_file

        if not test_data:
            return {"error": "No test data"}

        words = [w for w, _ in test_data]
        gold_syllables = [syls for _, syls in test_data]

        # Predict
        pred_syllables = [self.predict(w) for w in words]

        # Compute metrics
        total_words = len(words)
        correct_words = sum(1 for p, g in zip(pred_syllables, gold_syllables) if p == g)
        word_accuracy = correct_words / total_words if total_words > 0 else 0

        # Boundary-level metrics
        # Flatten to character-level boundaries
        gold_boundaries = []
        pred_boundaries = []

        for word, gold_syls, pred_syls in zip(words, gold_syllables, pred_syllables):
            # Gold boundaries
            pos = 0
            gold_bounds = set()
            for syl in gold_syls[:-1]:  # Don't count end of word
                pos += len(strip_diacritics(syl).lower())
                gold_bounds.add(pos)

            # Pred boundaries
            pos = 0
            pred_bounds = set()
            for syl in pred_syls[:-1]:
                pos += len(strip_diacritics(syl).lower())
                pred_bounds.add(pos)

            gold_boundaries.append(gold_bounds)
            pred_boundaries.append(pred_bounds)

        # Compute precision, recall, F1
        tp = sum(len(g & p) for g, p in zip(gold_boundaries, pred_boundaries))
        fp = sum(len(p - g) for g, p in zip(gold_boundaries, pred_boundaries))
        fn = sum(len(g - p) for g, p in zip(gold_boundaries, pred_boundaries))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        # Syllable count accuracy
        syl_count_correct = sum(
            1 for p, g in zip(pred_syllables, gold_syllables)
            if len(p) == len(g)
        )
        syl_count_acc = syl_count_correct / total_words if total_words > 0 else 0

        # Per-syllable-count breakdown
        by_count: dict[int, dict[str, int]] = {}
        for gold_syls, pred_syls in zip(gold_syllables, pred_syllables):
            count = len(gold_syls)
            if count not in by_count:
                by_count[count] = {"total": 0, "correct": 0}
            by_count[count]["total"] += 1
            if pred_syls == gold_syls:
                by_count[count]["correct"] += 1

        return {
            "total_words": total_words,
            "word_accuracy": word_accuracy,
            "boundary_precision": precision,
            "boundary_recall": recall,
            "boundary_f1": f1,
            "syllable_count_accuracy": syl_count_acc,
            "by_syllable_count": {
                k: v["correct"] / v["total"] if v["total"] > 0 else 0
                for k, v in by_count.items()
            },
        }

    def save(self, path: str | Path) -> None:
        """Save trained model to disk.

        Args:
            path: File path for the serialized model.
        """
        if self.crf is None:
            raise RuntimeError("No model to save. Train or load a model first.")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("wb") as f:
            pickle.dump(self.crf, f)

        self.model_path = str(path)
        print(f"Model saved to {path}")

    def load(self, path: str | Path) -> None:
        """Load trained model from disk.

        Args:
            path: File path of the serialized model.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {path}")

        with path.open("rb") as f:
            self.crf = pickle.load(f)  # noqa: S301

        self.model_path = str(path)
        print(f"Model loaded from {path}")


# --- CLI --------------------------------------------------------------------


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.crf_segmenter",
        description="Zolai CRF-based syllable segmenter (SylBreak4All M5)",
    )
    parser.add_argument(
        "--train",
        type=str,
        help="Path to training data JSONL (gold format)",
    )
    parser.add_argument(
        "--dev",
        type=str,
        help="Path to development data JSONL",
    )
    parser.add_argument(
        "--evaluate",
        type=str,
        help="Path to test data JSONL for evaluation",
    )
    parser.add_argument(
        "--load",
        type=str,
        help="Path to pre-trained model file",
    )
    parser.add_argument(
        "--save",
        type=str,
        help="Path to save trained model",
    )
    parser.add_argument(
        "--word",
        "-w",
        type=str,
        help="Segment a single word",
    )
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="Segment words from JSONL file",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output file for batch results (JSON)",
    )
    parser.add_argument(
        "--c1",
        type=float,
        default=0.1,
        help="L1 regularization (default: 0.1)",
    )
    parser.add_argument(
        "--c2",
        type=float,
        default=0.1,
        help="L2 regularization (default: 0.1)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=100,
        help="Max iterations (default: 100)",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        choices=["lbfgs", "l2sgd"],
        default="lbfgs",
        help="CRF algorithm (default: lbfgs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    segmenter = CRFSyllableSegmenter(model_path=args.load)

    if args.train:
        train_stats = segmenter.train(
            train_file=args.train,
            dev_file=args.dev,
            algorithm=args.algorithm,
            c1=args.c1,
            c2=args.c2,
            max_iterations=args.max_iter,
        )
        print(f"Training complete: {train_stats}")

        if args.save:
            segmenter.save(args.save)
        return 0

    if args.evaluate:
        if segmenter.crf is None and not args.load:
            print("Error: No model loaded. Use --load or --train first.", file=sys.stderr)
            return 1

        results = segmenter.evaluate(args.evaluate)
        print("Evaluation Results:")
        print(f"  Total words: {results.get('total_words', 0)}")
        print(f"  Word Accuracy: {results.get('word_accuracy', 0):.4f}")
        print(f"  Boundary Precision: {results.get('boundary_precision', 0):.4f}")
        print(f"  Boundary Recall: {results.get('boundary_recall', 0):.4f}")
        print(f"  Boundary F1: {results.get('boundary_f1', 0):.4f}")
        print(f"  Syllable Count Accuracy: {results.get('syllable_count_accuracy', 0):.4f}")
        print("  Per-syllable-count accuracy:")
        for count, acc in sorted(results.get("by_syllable_count", {}).items()):
            print(f"    {count}-syllable: {acc:.4f}")
        return 0

    if args.word:
        if segmenter.crf is None:
            print("Warning: No model loaded, using rule-based fallback", file=sys.stderr)
        result = segmenter.predict(args.word)
        print(f"Word: {args.word}")
        print(f"Syllables: {result}")
        print(f"Count: {len(result)}")
        return 0

    if args.file:
        input_path = Path(args.file)
        if not input_path.exists():
            print(f"Error: File not found: {input_path}", file=sys.stderr)
            return 1

        results = {}
        with input_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    word = data.get("word", "").strip()
                    if word:
                        results[word] = segmenter.predict(word)
                except json.JSONDecodeError:
                    continue

        if args.output:
            with Path(args.output).open("w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"Results written to {args.output}")
        else:
            for word, syls in results.items():
                print(f"{word}: {syls}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
