"""``get_knowledge_statistics`` is TTL-cached, and ``force=True`` bypasses it.

Twelve ``COUNT(*)`` scans of the production corpus measured 7.9s cold / 2.8s
warm (explorer#35: the Data page waited on it while the dashboard had already
loaded). The counts move on import, not on read, so a short cache is the fix —
and the cache must never serve one database's numbers for another, which is why
the key is the engine URL rather than a process global.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from zolai.rag.retrieve import STATS_CACHE_TTL_S, UnifiedRetriever, invalidate_statistics_cache


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    invalidate_statistics_cache()
    yield
    invalidate_statistics_cache()


@pytest.fixture()
def retriever(tmp_path) -> UnifiedRetriever:
    engine = create_engine(f"sqlite:///{tmp_path / 'stats.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE dictionary (zolai TEXT)"))
        conn.execute(text("INSERT INTO dictionary VALUES ('pasian'), ('vantung')"))
        conn.commit()
    return UnifiedRetriever(engine)


def test_second_call_is_served_from_cache(retriever: UnifiedRetriever) -> None:
    first = retriever.get_knowledge_statistics()
    assert first["dictionary entries"] == 2

    with retriever.engine.connect() as conn:
        conn.execute(text("INSERT INTO dictionary VALUES ('leitung')"))
        conn.commit()

    assert retriever.get_knowledge_statistics()["dictionary entries"] == 2, (
        "a read must not rescan the corpus (explorer#35: 7.9s per call)"
    )


def test_force_rescans(retriever: UnifiedRetriever) -> None:
    with retriever.engine.connect() as conn:
        conn.execute(text("INSERT INTO dictionary VALUES ('leitung')"))
        conn.commit()

    retriever.get_knowledge_statistics()
    assert retriever.get_knowledge_statistics(force=True)["dictionary entries"] == 3


def test_missing_tables_count_zero_not_raise(retriever: UnifiedRetriever) -> None:
    stats = retriever.get_knowledge_statistics()
    assert stats["Bible verses"] == 0
    assert len(stats) == 12


def test_cache_is_per_database(tmp_path) -> None:
    """Two engines in one process must not share a cached payload."""
    a = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    b = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    for engine, count in ((a, 1), (b, 3)):
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE dictionary (zolai TEXT)"))
            conn.execute(
                text("INSERT INTO dictionary VALUES (:v)"),
                [{"v": f"form{i}"} for i in range(count)],
            )
            conn.commit()

    # ``b`` is scanned and cached first, so reading ``a`` afterwards proves the
    # key is the database rather than the process.
    assert UnifiedRetriever(b).get_knowledge_statistics()["dictionary entries"] == 3
    assert UnifiedRetriever(a).get_knowledge_statistics()["dictionary entries"] == 1


def test_ttl_is_short_enough_to_self_refresh() -> None:
    """The cache is a latency fix, not a staleness contract: keep it seconds."""
    assert STATS_CACHE_TTL_S <= 60.0
