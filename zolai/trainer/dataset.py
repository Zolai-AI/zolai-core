"""Dataset building and training preparation."""

import json
import logging
import random
from collections import defaultdict

from ..config import config
from ..shared.utils import read_jsonl, write_jsonl

log = logging.getLogger(__name__)

#: Fields identifying the underlying source item. All rows sharing a key (both
#: translation directions, every instruction variant of one verse) stay in one
#: split; otherwise the same verse can be trained on and tested on at once.
_GROUP_FIELDS = ("reference", "ref", "pair_id", "verse_id")


def _group_key(record) -> str:
    if isinstance(record, dict):
        for field in _GROUP_FIELDS:
            value = record.get(field)
            if value not in (None, ""):
                return f"{field}:{value}"
        text = record.get("text") or record.get("zolai") or ""
        if text:
            return "text:" + " ".join(str(text).split()).casefold()
    return f"row:{id(record)}"


def _take_last_group(rows: list) -> list:
    """Pop one whole group off ``rows`` (all rows sharing the last row's key)."""
    key = _group_key(rows[-1])
    group = [r for r in rows if _group_key(r) == key]
    rest = [r for r in rows if _group_key(r) != key]
    rows[:] = rest
    return group


def _split_by_group(records: list, seed: int, val_ratio: float, test_ratio: float):
    """Split whole groups, never rows, so paired records cannot straddle splits."""
    groups: dict[str, list] = defaultdict(list)
    for record in records:
        groups[_group_key(record)].append(record)

    keys = sorted(groups)
    random.Random(seed).shuffle(keys)

    n = len(records)
    n_val = max(1, int(n * val_ratio))
    n_test = max(1, int(n * test_ratio))
    n_train = n - n_val - n_test
    if n_train < 1:
        raise ValueError(
            f"{n} records cannot fill train/val/test at "
            f"{val_ratio:.4f}/{test_ratio:.4f}"
        )

    train: list = []
    val: list = []
    test: list = []
    # Groups are shuffled, then poured in order until each bucket is full.
    # Overshoot stays inside the bucket, so ratios are approximate but groups
    # are never split.
    for key in keys:
        rows = groups[key]
        if len(train) < n_train:
            train.extend(rows)
        elif len(val) < n_val:
            val.extend(rows)
        else:
            test.extend(rows)
    # A single dominant group can empty a later bucket; borrow a whole group
    # rather than a row, so no key ends up on both sides. Lending is only
    # possible when the lender holds more than one group.
    def borrow(rows: list) -> list:
        return _take_last_group(rows) if len({_group_key(r) for r in rows}) > 1 else []

    for bucket, lenders in (("val", (train, test)), ("test", (val, train))):
        if (val if bucket == "val" else test):
            continue
        for lender in lenders:
            borrowed = borrow(lender)
            if borrowed:
                if bucket == "val":
                    val = borrowed
                else:
                    test = borrowed
                break
        else:
            raise ValueError(
                "cannot fill every split without sharing a record group; "
                "need at least 3 source groups"
            )
    return train, val, test


class DatasetBuilder:
    """Build and manage training datasets."""

    def __init__(self):
        self.paths = config.paths

    def build_splits(self, val_ratio: float = 0.02, test_ratio: float = 0.01, seed: int = 42) -> dict:
        """Build train/val/test splits from cleaned data."""
        # Find cleaned corpus
        candidates = [
            self.paths.data_cleaned / "cleaned_corpus.jsonl",
            self.paths.data_cleaned / "zolai_cleaned_sentences.jsonl",
            self.paths.data_cleaned / "zolai_unified.jsonl",
        ]
        input_path = None
        for c in candidates:
            if c.exists():
                input_path = c
                break

        if input_path is None:
            # Search in subdirectories
            for f in self.paths.data_cleaned.glob("**/*.jsonl"):
                input_path = f
                break

        if input_path is None:
            return {"error": "no cleaned data found", "searched": [str(c) for c in candidates]}

        records = read_jsonl(input_path)
        if not records:
            return {"error": "empty file", "path": str(input_path)}

        train, val, test = _split_by_group(records, seed, val_ratio, test_ratio)

        n = len(records)

        out_dir = self.paths.data_training
        out_dir.mkdir(parents=True, exist_ok=True)

        write_jsonl(train, out_dir / "train.jsonl")
        write_jsonl(val, out_dir / "val.jsonl")
        write_jsonl(test, out_dir / "test.jsonl")

        stats = {
            "total": n,
            "train": len(train),
            "val": len(val),
            "test": len(test),
            "source": str(input_path.name),
            "seed": seed,
        }

        with open(out_dir / "split_stats.json", "w") as f:
            json.dump(stats, f, indent=2)

        log.info("Splits built: %s", stats)
        return stats

    def prepare_training(self) -> dict:
        """Prepare training data from cleaned corpus."""
        return self.build_splits()

    def export_jsonl(self) -> dict:
        """Export to HuggingFace-compatible JSONL format."""
        hf_dir = self.paths.data_training / "hf_format"
        hf_dir.mkdir(parents=True, exist_ok=True)

        exported = 0
        for split in ["train", "val", "test"]:
            src = self.paths.data_training / f"{split}.jsonl"
            if not src.exists():
                continue
            records = read_jsonl(src)
            dst = hf_dir / f"{split}.jsonl"
            with open(dst, "w", encoding="utf-8") as f:
                for rec in records:
                    text = rec.text if hasattr(rec, "text") else str(rec)
                    source = rec.source if hasattr(rec, "source") else "unknown"
                    f.write(json.dumps({"text": text, "source": source}, ensure_ascii=False) + "\n")
            exported += len(records)

        log.info("Exported %d records to %s", exported, hf_dir)
        return {"exported": exported, "path": str(hf_dir)}
