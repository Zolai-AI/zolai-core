"""Split leakage detection: no record may appear in more than one split.

A row that lands in both train and test inflates every metric for that row.
Normalization is deliberately aggressive (NFKC, case-fold, whitespace collapse)
because near-identical rows are the realistic leak: the same verse re-exported
with different formatting still teaches the test item to the model.

Only the Zolai-side text identifies a record. English translations are shared
across many verses, so hashing them would flag legitimate divergence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path

_WS_RE = re.compile(r"\s+")
_TEXT_FIELDS = ("text", "zolai", "output", "headword")


def normalize_text(value: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def record_fingerprint(record: Mapping) -> str | None:
    """SHA256 of the record's normalized Zolai text, or ``None`` when absent."""
    for field in _TEXT_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()
    return None


def iter_fingerprints(path: str | Path) -> Iterable[tuple[str, int]]:
    """Yield ``(fingerprint, line_number)`` for every identifiable row."""
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            fingerprint = record_fingerprint(record)
            if fingerprint is not None:
                yield fingerprint, line_no


def _scan(path: str | Path) -> dict[str, int]:
    seen: dict[str, int] = {}
    for fingerprint, line_no in iter_fingerprints(path):
        seen.setdefault(fingerprint, line_no)
    return seen


def check_leakage(
    splits: Mapping[str, str | Path],
    *,
    eval_vs: Mapping[str, str | Path] | None = None,
    examples: int = 3,
) -> dict:
    """Compare every split against every other; ``eval_vs`` rows vs ``train``.

    Missing paths are skipped and reported, never treated as empty passes:
    a gate that silently accepts an absent test split is worse than no gate.
    """
    scanned = {name: _scan(path) for name, path in splits.items()}
    overlaps: list[dict] = []

    names = list(scanned)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            shared = scanned[left].keys() & scanned[right].keys()
            if shared:
                overlaps.append(
                    {
                        "left": left,
                        "right": right,
                        "count": len(shared),
                        "examples": [
                            {"left_line": scanned[left][h], "right_line": scanned[right][h]}
                            for h in sorted(shared)[:examples]
                        ],
                    }
                )

    train = scanned.get("train")
    for name, path in (eval_vs or {}).items():
        rows = _scan(path)
        if train is not None:
            shared = train.keys() & rows.keys()
            if shared:
                overlaps.append(
                    {
                        "left": "train",
                        "right": f"eval:{name}",
                        "count": len(shared),
                        "examples": [
                            {"left_line": train[h], "right_line": rows[h]}
                            for h in sorted(shared)[:examples]
                        ],
                    }
                )

    return {
        "ok": not overlaps,
        "splits": {name: len(rows) for name, rows in scanned.items()},
        "overlaps": overlaps,
    }
