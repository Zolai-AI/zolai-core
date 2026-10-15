"""
Fine-Tuning Dataset Builder - Creates training datasets directly from database.
No JSON/JSONL files - uses database directly for training.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...data.repositories import get_engine

logger = logging.getLogger(__name__)


@dataclass
class DatasetSplit:
    """Train/validation split information."""

    train_indices: list
    val_indices: list
    train_count: int
    val_count: int
    total_count: int


class FineTuningDatasetBuilder:
    """Builds training datasets directly from database - no JSON/JSONL files."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self._indices_cache: dict = {}
        self._id_cache: dict = {}

    # ── Streaming ──────────────────────────────────────────────────────────

    def get_training_stream(
        self,
        format: str = "alpaca",
        split: str = "train",
        batch_size: int = 32,
        min_confidence: float = 0.7,
        status_filter: str = "approved",
        seed: int = 42,
    ):
        """Stream training samples directly from the database.

        Yields samples in the requested format (alpaca, chatml, sharegpt).
        """
        signal_ids = self._get_split_signal_ids(split, min_confidence, status_filter, seed)
        if not signal_ids:
            logger.warning(f"No samples found for split={split}")
            return

        for batch_ids in self._batched(signal_ids, batch_size):
            for sample in self._fetch_samples(batch_ids):
                yield self._format_sample(sample, format)

    def get_sample_generator(
        self,
        format: str = "alpaca",
        split: str = "train",
        batch_size: int = 32,
        min_confidence: float = 0.7,
    ):
        """Get a generator that yields formatted samples."""
        return self.get_training_stream(
            format=format,
            split=split,
            batch_size=batch_size,
            min_confidence=min_confidence,
        )

    # ── Split handling ─────────────────────────────────────────────────────

    def _get_split_signal_ids(
        self,
        split: str,
        min_confidence: float,
        status_filter: str = "approved",
        seed: int = 42,
    ) -> list:
        """Resolve the ordered signal ids belonging to a split (cached)."""
        cache_key = f"{status_filter}:{min_confidence}:{seed}"
        all_ids = self._id_cache.get(cache_key)
        if all_ids is None:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT signal_id FROM learning_signals
                        WHERE status = :status AND confidence >= :min_conf
                        ORDER BY signal_id
                    """),
                    {"status": status_filter, "min_conf": min_confidence},
                ).fetchall()
            all_ids = [row[0] for row in rows]
            self._id_cache[cache_key] = all_ids

        if not all_ids:
            return []

        split_key = f"{cache_key}:{split}"
        split_info = self._indices_cache.get(split_key)
        if split_info is None:
            split_info = self._create_split_indices(len(all_ids), seed=seed)
            self._indices_cache[split_key] = split_info

        indices = split_info.train_indices if split == "train" else split_info.val_indices
        return [all_ids[i] for i in indices]

    def _create_split_indices(self, total: int, val_ratio: float = 0.1, seed: int = 42) -> DatasetSplit:
        """Create deterministic train/val split indices."""
        rng = random.Random(seed)
        indices = list(range(total))
        rng.shuffle(indices)

        val_count = max(1, int(total * val_ratio))
        val_indices = set(indices[:val_count])
        train_indices = [i for i in indices if i not in val_indices]

        return DatasetSplit(
            train_indices=sorted(train_indices),
            val_indices=sorted(val_indices),
            train_count=len(train_indices),
            val_count=len(val_indices),
            total_count=total,
        )

    @staticmethod
    def _batched(items: list, batch_size: int):
        """Yield consecutive slices of at most ``batch_size`` items."""
        size = max(1, batch_size)
        for start in range(0, len(items), size):
            yield items[start:start + size]

    def _fetch_samples(self, signal_ids: list) -> list:
        """Fetch full rows for the given signal ids."""
        if not signal_ids:
            return []

        rows = []
        with self.engine.connect() as conn:
            for chunk in self._batched(list(signal_ids), 500):
                placeholders = ", ".join(f":id{i}" for i in range(len(chunk)))
                params = {f"id{i}": value for i, value in enumerate(chunk)}
                rows.extend(
                    dict(row._mapping)
                    for row in conn.execute(
                        text(f"""
                            SELECT signal_id, query, response, feedback_type,
                                   feedback_value, confidence, metadata
                            FROM learning_signals
                            WHERE signal_id IN ({placeholders})
                        """),
                        params,
                    ).fetchall()
                )
        return rows

    def _format_sample(self, sample: dict, format: str) -> dict:
        """Format a sample in the specified format."""
        query = sample.get("query", "") or ""
        output = sample.get("feedback_value") or sample.get("response", "") or ""

        if format == "alpaca":
            return {"instruction": query, "input": "", "output": output}
        if format == "chatml":
            return {
                "messages": [
                    {"role": "system", "content": "You are a Zolai language assistant."},
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": output},
                ]
            }
        if format == "sharegpt":
            return {
                "conversations": [
                    {"from": "human", "value": query},
                    {"from": "gpt", "value": output},
                ]
            }
        raise ValueError(f"Unknown format: {format}")

    # ── Bible-backed samples ───────────────────────────────────────────────

    def get_bible_training_stream(
        self,
        format: str = "alpaca",
        split: str = "train",
        batch_size: int = 32,
        source_filter: str = "bible",
    ):
        """Stream Bible-based training samples (both translation directions)."""
        if source_filter != "bible":
            logger.warning(f"Unsupported source_filter={source_filter!r}; using bible_verses")

        with self.engine.connect() as conn:
            total = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
            """)).scalar() or 0

            if total == 0:
                logger.warning("No parallel bible_verses found")
                return

        # Deterministic boundary: the tail 10% of refs is the validation half.
        boundary = max(1, int(total * 0.9)) if split != "train" else total
        offset = boundary if split != "train" else 0
        size = max(1, batch_size)

        start = offset
        while start < total:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT zo_tdb77, en_kJV, ref FROM bible_verses
                        WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
                        ORDER BY ref LIMIT :limit OFFSET :offset
                    """),
                    {"limit": size, "offset": start},
                ).fetchall()
            if not rows:
                break

            for row in rows:
                zo = row.zo_tdb77
                en = row.en_kJV
                ref = row.ref

                if not zo or not en:
                    continue

                # Yield both directions
                yield self._format_bible_sample(zo, en, ref, "zo2en")
                yield self._format_bible_sample(en, zo, ref, "en2zo")

            start += size

    def _format_bible_sample(self, source: str, target: str, ref: str, direction: str) -> dict:
        """Format Bible verse as training sample."""
        if direction == "zo2en":
            return {
                "instruction": f"Translate this Zolai Bible verse to English ({ref})",
                "input": source,
                "output": target,
            }
        return {
            "instruction": f"Translate this English Bible verse to Zolai ({ref})",
            "input": source,
            "output": target,
        }

    # ── Stats ──────────────────────────────────────────────────────────────

    def get_split_counts(self, min_confidence: float = 0.7, status_filter: str = "approved") -> dict:
        """Get train/validation counts."""
        with self.engine.connect() as conn:
            total = conn.execute(
                text("""
                    SELECT COUNT(*) FROM learning_signals
                    WHERE status = :status AND confidence >= :min_conf
                """),
                {"status": status_filter, "min_conf": min_confidence},
            ).scalar()

        if not total:
            return {"train": 0, "validation": 0, "total": 0}

        val_count = max(1, int(total * 0.1))
        return {
            "train": total - val_count,
            "validation": val_count,
            "total": total,
        }

    def format_sample(self, sample: dict, format: str = "alpaca") -> dict:
        """Public alias for :meth:`_format_sample`."""
        return self._format_sample(sample, format)

    def parse_metadata(self, raw) -> dict:
        """Parse a ``metadata`` column into a dict (tolerates plain text)."""
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
