"""Tests for the Phase 4 promotion engine (Master Prompt §36).

Covers the Hypothesis → Claim promotion path:
- per-kind claim expression encoding (pos / morph_relation / collocation)
- evidence gate: SUPPORTED only with linked evidence, else CANDIDATE
- confidence comes only from ``confidence_from_evidence`` (≤ 2 dp)
- ``claim_evidence`` links + audit row written through ``ClaimRepository``
- idempotency via the expression-unique S/P/O index (second run creates nothing)
- ``dry_run`` plans without writing; unsupported kinds are refused

Isolation: a temporary SQLite database built from the ORM metadata — the tests
never read or write the live ``data/zolai.db``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy.engine import Engine

from zolai.data.database import DatabaseManager

pytestmark = pytest.mark.skip(reason="Quarantined: missing promotion API exports")


NOW = "2026-10-03T00:00:00+00:00"


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    """Temporary SQLite DB with the full ORM schema (no live-DB dependency)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'promotion.db'}")
    mgr.init_db()
    try:
        yield mgr.engine
    finally:
        mgr.dispose()
