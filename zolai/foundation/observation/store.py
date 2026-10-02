"""Write-side facade for the observation layer (§18: observations zone).

Three jobs:

- ``ensure_tables`` — idempotent bootstrap of the three Phase 2 tables;
- ``build_*_row``   — serialization of pipeline output into table rows;
- ``ObservationStore`` — chunked insert/upsert delegation to the repositories.

The DDL itself is *not* re-typed here: the store reuses the constants and the
bootstrap routine owned by :mod:`zolai.data.migrations`, so the migration path
and the build path can never drift apart.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.engine import Engine

from ...data.migrations import create_observation_tables
from ...data.repositories.observation import ObservationRepository, WordStatsRepository
from .sentences import Sentence

__all__ = [
    "ObservationStore",
    "build_observation_row",
    "build_stats_row",
    "ensure_tables",
]


class _EngineOnlyManager:
    """Structural stand-in exposing ``.engine`` for ``create_observation_tables``.

    That migration function reads exactly one attribute (``mgr.engine``); this
    keeps a single DDL implementation without forcing a second
    ``DatabaseManager`` (and therefore a second engine) into the build path.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine


def ensure_tables(engine: Engine) -> dict[str, list[str]]:
    """Create the Phase 2 tables/indexes if missing (safe to call every run)."""
    return create_observation_tables(_EngineOnlyManager(engine))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_observation_row(
    sentence: Sentence,
    tokens: list[str],
    *,
    extractor: str,
    pipeline_version: str,
    zvs_corrected: bool,
) -> dict[str, Any]:
    """Serialize one extracted sentence as an ``observations`` row.

    ``context`` carries the document-level locator (``source_name:document``)
    — the surrounding context available without a second read of the source.
    """

    return {
        "text": sentence.text,
        "tokens": json.dumps(tokens, ensure_ascii=False),
        "context": f"{sentence.source.name}:{sentence.document_id}",
        "source_ref": sentence.source_ref,
        "source_id": sentence.source_id,
        "document_id": sentence.document_id,
        "sentence_id": sentence.sentence_id,
        "method": sentence.source.method,
        "extractor": extractor,
        "metadata": json.dumps(
            {
                "token_count": len(tokens),
                "zvs_corrected": zvs_corrected,
                "pipeline_version": pipeline_version,
            },
            ensure_ascii=False,
        ),
    }


def build_stats_row(
    word: str,
    *,
    scalars: dict[str, int | float],
    surface_forms: list[dict[str, Any]],
    contexts: dict[str, Any],
    neighbors: list[dict[str, Any]],
    collocations: list[dict[str, Any]],
    attestation: dict[str, Any],
    seen_at: str,
    pipeline_version: str,
) -> dict[str, Any]:
    """Serialize one word's derived roll-up as a ``word_observation_stats`` row."""
    return {
        "normalized_form": word,
        "frequency": int(scalars["frequency"]),
        "doc_freq": int(scalars["doc_freq"]),
        "sent_freq": int(scalars["sent_freq"]),
        "source_count": int(scalars["source_count"]),
        "diversity": float(scalars["diversity"]),
        "surface_forms": json.dumps(surface_forms, ensure_ascii=False),
        "contexts": json.dumps(contexts, ensure_ascii=False),
        "neighbors": json.dumps(neighbors, ensure_ascii=False),
        "collocations": json.dumps(collocations, ensure_ascii=False),
        "attestation": json.dumps(attestation, ensure_ascii=False),
        "first_seen": seen_at,
        "last_seen": seen_at,
        "pipeline_version": pipeline_version,
        "updated_at": _now(),
    }


class ObservationStore:
    """Table bootstrap + bulk write delegation used by the build pipeline.

    Every bulk method forwards its ``conn`` so a pipeline holding a single
    long-lived transaction (SQLite TEMP pair table, single-writer discipline)
    never opens a second connection mid-build.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.observations = ObservationRepository(engine)
        self.stats = WordStatsRepository(engine)

    def ensure_tables(self) -> dict[str, list[str]]:
        return ensure_tables(self.engine)

    def warm(self) -> None:
        """Force lazy table reflection before a long transaction starts."""
        _ = self.observations.table, self.stats.table

    def insert_observations(
        self, records: list[dict[str, Any]], *, conn: Any = None
    ) -> int:
        return self.observations.insert_ignore(records, conn=conn)

    def upsert_stats(self, rows: list[dict[str, Any]], *, conn: Any = None) -> int:
        return self.stats.upsert_many(rows, conn=conn)

    def count_observations(self) -> int:
        return int(self.observations.count())
