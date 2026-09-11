"""Feedback and correction learning system for Zolai translations.

Records user corrections, stores them in JSONL, and injects overrides
into RAG retrieval to improve translation accuracy over time.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FeedbackStore:
    """Persistent correction store backed by JSONL.

    Each line is a correction record:
        {"word", "original", "corrected", "user", "reason", "timestamp"}

    The *latest* correction for a given word wins as the override.
    """

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "data"
                / "feedback"
                / "corrections.jsonl"
            )
        self.path = path
        self._records: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._records is not None:
            return
        self._records = []
        if self.path.exists():
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    def _save(self) -> None:
        """Flush all records to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            for rec in self._records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        word: str,
        original: str,
        corrected: str,
        user: str = "anonymous",
        reason: str = "",
    ) -> dict[str, Any]:
        """Append a correction record and persist."""
        self._ensure_loaded()
        rec: dict[str, Any] = {
            "word": word.lower(),
            "original": original,
            "corrected": corrected,
            "user": user,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._records.append(rec)
        self._save()
        return rec

    def get_corrections(self, word: str) -> list[dict[str, Any]]:
        """Return all corrections for *word* (newest last)."""
        self._ensure_loaded()
        w = word.lower()
        return [r for r in self._records if r.get("word") == w]

    def get_override(self, word: str) -> str | None:
        """Return the latest corrected translation for *word*, or None."""
        corrections = self.get_corrections(word)
        if not corrections:
            return None
        return corrections[-1].get("corrected")

    def stats(self) -> dict[str, Any]:
        """Summary statistics for all corrections."""
        self._ensure_loaded()
        words: Counter[str] = Counter()
        users: Counter[str] = Counter()
        for rec in self._records:
            words[rec.get("word", "")] += 1
            users[rec.get("user", "anonymous")] += 1
        return {
            "total_corrections": len(self._records),
            "unique_words": len(words),
            "top_words": words.most_common(10),
            "top_correctors": users.most_common(10),
        }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _cli() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(
        description="Zolai Feedback & Correction System",
    )
    sub = parser.add_subparsers(dest="command")

    rec = sub.add_parser("record", help="Record a correction")
    rec.add_argument("word", help="Word being corrected")
    rec.add_argument("original", help="Original translation")
    rec.add_argument("corrected", help="Corrected translation")
    rec.add_argument("--user", default="anonymous", help="User who corrected")
    rec.add_argument("--reason", default="", help="Reason for correction")

    sub.add_parser("stats", help="Show correction statistics")

    args = parser.parse_args()
    store = FeedbackStore()

    if args.command == "record":
        result = store.record(
            args.word,
            args.original,
            args.corrected,
            user=args.user,
            reason=args.reason,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "stats":
        print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
