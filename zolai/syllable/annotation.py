"""Human syllable annotation CLI for Zolai.

Provides interactive tool for creating gold-standard syllable annotations.
Supports stratified sampling, progress saving, resume capability, and CoNLL export.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from .rules import (
    DIGRAPHS,
    DIPHTHONGS,
    VALID_CODAS,
    VOWELS,
)
from .segmenter import RuleBasedSegmenter


@dataclass
class AnnotationEntry:
    """A single annotated word entry."""
    word: str
    syllables: list[str]
    syllable_count: int
    source: str
    frequency: int = 0
    pos: str = ""
    auto_segmentation: list[str] | None = None
    verified: bool = True

    def to_conll(self) -> str:
        """Convert to CoNLL format (chars + BIO tags)."""
        chars = list(self.word)
        tags = []
        for i, syl in enumerate(self.syllables):
            for j, _ch in enumerate(syl):
                tags.append("B" if j == 0 else "I")
        if len(chars) != len(tags):
            raise ValueError(f"Char/tag mismatch for {self.word}")
        return " ".join(chars) + "\n" + " ".join(tags)

    def to_jsonl(self) -> str:
        """Convert to JSONL format."""
        return json.dumps(asdict(self), ensure_ascii=False)


class SyllableAnnotator:
    """CLI tool for human syllable annotation."""

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize annotator with database connection.

        Args:
            db_path: Path to SQLite database. Defaults to data/zolai.db
        """
        if db_path is None:
            db_path = "/home/peter/Documents/Projects/zolai-ai/data/zolai.db"
        self.db_path = db_path
        self.segmenter = RuleBasedSegmenter()

    def _validate_syllables(self, word: str, syllables: list[str]) -> tuple[bool, str]:
        """Validate syllable segmentation against Zolai phonotactics.

        Args:
            word: Original word
            syllables: List of syllable strings

        Returns:
            (is_valid, error_message)
        """
        # Check reconstruction
        if "".join(syllables) != word:
            return False, f"Syllables {syllables} don't reconstruct word {word}"

        # Validate each syllable
        for syl in syllables:
            if not syl:
                return False, "Empty syllable found"

            # Must have at least one vowel
            has_vowel = any(c.lower() in VOWELS for c in syl)
            if not has_vowel:
                return False, f"Syllable '{syl}' has no vowel"

        return True, ""

    def _get_auto_segmentation(self, word: str) -> list[str]:
        """Get automatic segmentation for a word."""
        return self.segmenter.segment(word)

    def _get_syllable_data_words(self, max_syllables: int, limit_per_count: int) -> list[dict]:
        """Get words from syllable_data table, stratified by syllable count."""
        results = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for count in range(1, max_syllables + 1):
                cursor = conn.execute("""
                    SELECT word, syllables, syllable_count, source_table as source
                    FROM syllable_data
                    WHERE word GLOB '[a-z]*'
                      AND word NOT GLOB '*[^a-z]*'
                      AND word NOT GLOB '*[0-9]*'
                      AND length(word) BETWEEN 2 AND 20
                      AND syllable_count = ?
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (count, limit_per_count))
                for row in cursor:
                    try:
                        syl_list = json.loads(row["syllables"])
                        if syl_list and len(syl_list) == row["syllable_count"]:
                            results.append({
                                "word": row["word"],
                                "syllables": syl_list,
                                "syllable_count": row["syllable_count"],
                                "source": "syllable_data",
                                "frequency": 0,
                                "pos": "",
                            })
                    except (json.JSONDecodeError, KeyError):
                        continue
        return results

    def _get_dictionary_words(self, max_syllables: int, limit_per_count: int) -> list[dict]:
        """Get words from dictionary table, stratified by syllable count.

        Note: We sample more words from SQL and segment in Python,
        since syllable count isn't indexed in the dictionary table.
        """
        # Source priority for dictionary sources
        source_priority = {
            "zvs_master": 0,
            "supplement": 1,
            "zo_en_wordlist": 2,
            "singlewords": 3,
        }

        # First get candidate words with their source priority
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT zolai as word, pos, source
                FROM dictionary
                WHERE zolai GLOB '[a-z]*'
                  AND zolai NOT GLOB '*[^a-z]*'
                  AND zolai NOT GLOB '*[0-9]*'
                  AND length(zolai) BETWEEN 2 AND 20
                  AND is_deleted = 0
                ORDER BY RANDOM()
                LIMIT ?
            """, (limit_per_count * max_syllables * 5,))  # Get extra for stratification

            candidates_by_count: dict[int, list[dict]] = defaultdict(list)
            for row in cursor:
                word = row["word"]
                syllables = self.segmenter.segment(word)
                if not syllables:
                    continue
                syl_count = len(syllables)
                if syl_count > max_syllables:
                    continue
                priority = source_priority.get(row["source"], 4)
                candidates_by_count[syl_count].append({
                    "word": word,
                    "syllables": syllables,
                    "syllable_count": syl_count,
                    "source": f"dictionary:{row['source']}",
                    "frequency": 0,
                    "pos": row["pos"] or "",
                    "_priority": priority,
                })

        # Now sample from each syllable count bucket
        results = []
        for count in range(1, max_syllables + 1):
            bucket = candidates_by_count.get(count, [])
            bucket.sort(key=lambda x: x["_priority"])
            for item in bucket[:limit_per_count]:
                item.pop("_priority", None)
                results.append(item)
        return results

    def sample_words(
        self,
        n: int = 500,
        min_freq: int = 5,
        max_syllables: int = 6,
    ) -> list[dict]:
        """Sample words for annotation with diversity.

        Stratifies by:
        - Syllable count (1-6)
        - Source priority (zvs_master > supplement > zo_en_wordlist > syllable_data)

        Args:
            n: Number of words to sample
            min_freq: Minimum frequency (for corpus words) - currently unused
            max_syllables: Maximum syllable count

        Returns:
            List of candidate word dicts
        """
        # Calculate target per syllable count
        syllable_counts = list(range(1, max_syllables + 1))
        target_per_count = max(1, n // len(syllable_counts))

        # Get words from both sources
        syl_data_words = self._get_syllable_data_words(max_syllables, target_per_count)
        dict_words = self._get_dictionary_words(max_syllables, target_per_count)

        # Combine and deduplicate (prefer higher priority source)
        seen = {}
        source_priority = {
            "dictionary:zvs_master": 0,
            "dictionary:supplement": 1,
            "dictionary:zo_en_wordlist": 2,
            "dictionary:singlewords": 3,
            "syllable_data": 4,
        }

        all_candidates = syl_data_words + dict_words
        for c in all_candidates:
            word = c["word"]
            priority = source_priority.get(c["source"], 5)
            if word not in seen or priority < source_priority.get(seen[word]["source"], 5):
                seen[word] = c

        # Balance by syllable count
        unique = list(seen.values())
        by_count: dict[int, list[dict]] = defaultdict(list)
        for c in unique:
            by_count[c["syllable_count"]].append(c)

        sampled = []
        for count in syllable_counts:
            bucket = by_count.get(count, [])
            sampled.extend(bucket[:target_per_count])

        # If we need more, fill from remaining
        if len(sampled) < n:
            remaining = [c for c in unique if c not in sampled]
            remaining.sort(key=lambda x: source_priority.get(x["source"], 5))
            sampled.extend(remaining[: n - len(sampled)])

        return sampled[:n]

    def annotate_word(self, word: str) -> list[str] | None:
        """Interactive syllable annotation for one word.

        Shows word and auto-segmentation, accepts user input.
        Returns annotated syllables or None if skipped.

        Args:
            word: Word to annotate

        Returns:
            List of syllable strings, or None to skip
        """
        auto_syl = self._get_auto_segmentation(word)

        print(f"\n{'='*60}")
        print(f"Word: {word}")
        print(f"Auto-segmentation: {' + '.join(auto_syl)} ({len(auto_syl)} syllables)")
        print(f"Phonotactics: digraphs={DIGRAPHS}, diphthongs={DIPHTHONGS}")
        print(f"Valid codas: {VALID_CODAS}")
        print("Enter syllables separated by space or hyphen (e.g., 'van tung' or 'van-tung')")
        print("Press Enter to accept auto, 's' to skip, 'q' to quit")

        while True:
            user_input = input("> ").strip()

            if user_input.lower() == "q":
                return "QUIT"
            if user_input.lower() == "s":
                return None
            if not user_input:
                # Accept auto-segmentation
                return auto_syl

            # Parse user input (space or hyphen separated)
            if " " in user_input:
                syllables = [s.strip() for s in user_input.split(" ") if s.strip()]
            elif "-" in user_input:
                syllables = [s.strip() for s in user_input.split("-") if s.strip()]
            else:
                syllables = [user_input]

            # Validate
            is_valid, error = self._validate_syllables(word, syllables)
            if is_valid:
                print(f"Accepted: {' + '.join(syllables)} ({len(syllables)} syllables)")
                return syllables
            else:
                print(f"Error: {error}")
                print("Try again (e.g., 'van tung')")

    def run_annotation_session(
        self,
        words: list[dict],
        output_path: str,
        resume: bool = True,
    ) -> list[AnnotationEntry]:
        """Run full annotation session with progress saving.

        Args:
            words: List of word dicts to annotate
            output_path: Path to save progress (JSONL)
            resume: Whether to resume from existing progress

        Returns:
            List of completed annotations
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing progress if resuming
        completed: dict[str, AnnotationEntry] = {}
        if resume and output_path.exists():
            with output_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = AnnotationEntry(**data)
                        completed[entry.word] = entry
                    except (json.JSONDecodeError, TypeError):
                        continue
            print(f"Resumed: {len(completed)} words already annotated")

        # Filter out already completed
        remaining = [w for w in words if w["word"] not in completed]
        print(f"Remaining: {len(remaining)} words to annotate")

        for i, word_data in enumerate(remaining, 1):
            word = word_data["word"]
            print(f"\n[{i}/{len(remaining)}] {word}")

            result = self.annotate_word(word)

            if result == "QUIT":
                print("\nQuitting... Progress saved.")
                break
            if result is None:
                print("Skipped.")
                continue

            entry = AnnotationEntry(
                word=word,
                syllables=result,
                syllable_count=len(result),
                source=word_data.get("source", "unknown"),
                frequency=word_data.get("frequency", 0),
                pos=word_data.get("pos", ""),
                auto_segmentation=word_data.get("syllables"),
                verified=True,
            )
            completed[word] = entry

            # Save progress after each word
            with output_path.open("a", encoding="utf-8") as f:
                f.write(entry.to_jsonl() + "\n")

        return list(completed.values())

    def export_gold_standard(
        self,
        output_path: str,
        entries: list[AnnotationEntry] | None = None,
        format: str = "conll",
    ) -> None:
        """Export annotations as CoNLL or JSONL.

        Args:
            output_path: Output file path
            entries: Annotations to export (loads from file if None)
            format: "conll" or "jsonl"
        """
        if entries is None:
            # Load from file
            entries = []
            with Path(output_path).open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(AnnotationEntry(**json.loads(line)))

        output_path = Path(output_path)
        if format == "conll":
            conll_path = output_path.with_suffix(".conll")
            with conll_path.open("w", encoding="utf-8") as f:
                for i, entry in enumerate(entries):
                    if i > 0:
                        f.write("\n")
                    f.write(entry.to_conll() + "\n")
            print(f"Exported {len(entries)} entries to {conll_path}")
        elif format == "jsonl":
            with output_path.open("w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(entry.to_jsonl() + "\n")
            print(f"Exported {len(entries)} entries to {output_path}")
        else:
            raise ValueError(f"Unknown format: {format}")

    def print_stats(self, entries: list[AnnotationEntry]) -> None:
        """Print annotation statistics."""
        if not entries:
            print("No entries")
            return

        syl_counts = defaultdict(int)
        source_counts = defaultdict(int)
        pos_counts = defaultdict(int)

        for e in entries:
            syl_counts[e.syllable_count] += 1
            source_counts[e.source] += 1
            if e.pos:
                pos_counts[e.pos] += 1

        print(f"\n{'='*60}")
        print("ANNOTATION STATISTICS")
        print(f"{'='*60}")
        print(f"Total words: {len(entries)}")
        print(f"Syllable distribution: {dict(sorted(syl_counts.items()))}")
        print(f"Source distribution: {dict(source_counts)}")
        if pos_counts:
            print(f"POS distribution: {dict(pos_counts)}")


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zolai.syllable.annotation",
        description="Human syllable annotation tool for Zolai (SylBreak4All)",
    )
    parser.add_argument(
        "--sample",
        "-s",
        type=int,
        default=500,
        help="Number of words to sample for annotation",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="data/syllable/gold_human.jsonl",
        help="Output file path for annotations (JSONL)",
    )
    parser.add_argument(
        "--export",
        "-e",
        type=str,
        choices=["conll", "jsonl", "both"],
        help="Export format after annotation",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Start fresh, don't resume from existing progress",
    )
    parser.add_argument(
        "--max-syllables",
        type=int,
        default=6,
        help="Maximum syllable count to include",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Just print stats on existing annotations",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_cli()
    args = parser.parse_args(argv)

    annotator = SyllableAnnotator(db_path=args.db_path)

    if args.stats_only:
        # Load existing annotations and print stats
        output_path = Path(args.output)
        if output_path.exists():
            entries = []
            with output_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(AnnotationEntry(**json.loads(line)))
            annotator.print_stats(entries)
        else:
            print(f"No annotation file found at {args.output}")
        return 0

    # Sample words
    words = annotator.sample_words(
        n=args.sample,
        max_syllables=args.max_syllables,
    )
    print(f"Sampled {len(words)} words for annotation")

    # Print sample preview
    print("\nSample preview (first 10):")
    for w in words[:10]:
        print(f"  {w['word']:20s} | {w['syllable_count']} syl | {w['source']:30s} | {' + '.join(w['syllables'])}")

    # Run annotation session
    entries = annotator.run_annotation_session(
        words=words,
        output_path=args.output,
        resume=not args.no_resume,
    )

    # Print stats
    annotator.print_stats(entries)

    # Export if requested
    if args.export:
        if args.export in ("conll", "both"):
            annotator.export_gold_standard(args.output, entries, format="conll")
        if args.export in ("jsonl", "both"):
            annotator.export_gold_standard(args.output, entries, format="jsonl")

    return 0


if __name__ == "__main__":
    sys.exit(main())
