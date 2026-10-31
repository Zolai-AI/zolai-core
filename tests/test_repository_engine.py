"""Regression tests for repository engine configuration and bootstrap."""

from __future__ import annotations

from sqlalchemy import inspect, text

from zolai.data.repositories import get_engine


def test_repository_engine_uses_canonical_sqlite_settings(tmp_path) -> None:
    engine = get_engine(tmp_path / "settings.db")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert conn.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == 30000
    finally:
        engine.dispose()


def test_repository_engine_bootstraps_fresh_schema(tmp_path) -> None:
    engine = get_engine(tmp_path / "fresh.db")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"dictionary", "knowledge_claims", "knowledge_versions"}.issubset(tables)
        assert "knowledge_vectors" in tables
    finally:
        engine.dispose()
