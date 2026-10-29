"""Read-only API smoke over the public GET surface (P2).

One representative request per public read route — sane status, stable shape —
so a broken mount, renamed key or accidental auth flip fails fast without
depending on the 2.4GB canonical DB (throwaway seeded SQLite instead):

- ``GET /health``
- ``GET /api/v1/lexicon/{word}`` (hit + miss → 200, consumers branch on ``found``)
- ``GET /api/v1/linguistics/pos``
- ``GET /api/v1/predictions/health`` (reports the D2 engine mode)
- ``GET /api/v1/predictions/next``
- ``GET /api/v1/records?table=dictionary``

Auth posture: warn-mode dual-accept (no key presented) — the smoke proves the
routes are reachable, not that enforce mode accepts anonymous callers.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.engines import AI_KEY_ENV_VARS, DEFAULT_ENGINE_MODE, ENGINE_MODE_ENV

# ── Fixtures ────────────────────────────────────────────────────────────────

_SEED_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT,
    pos TEXT,
    source TEXT,
    syllables TEXT,
    syllable_count INTEGER,
    pos_canonical TEXT,
    pos_candidates TEXT DEFAULT '[]',
    pos_evidence TEXT DEFAULT 'unknown',
    morph_features TEXT DEFAULT '{}',
    review_status TEXT DEFAULT 'unknown',
    confidence REAL
);
INSERT INTO dictionary VALUES
    (1, 'pasian', 'God', 'noun', 'test', 'pa|sian', 2, 'N', '["N","PROPER"]', 'lexicon', '{}', 'unknown', 0.9),
    (2, 'gam', 'earth, land', 'noun', 'test', 'gam', 1, 'N', '["N"]', 'lexicon', '{}', 'unknown', 0.8),
    (3, 'tapa', 'son, life', 'noun', 'test', 'ta|pa', 2, 'N', '["N"]', 'lexicon', '{}', 'unknown', 0.7);
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    translations TEXT,
    pos TEXT,
    source TEXT
);
INSERT INTO dictionary_en_zo VALUES
    (1, 'god', 'pasian', 'noun', 'test'),
    (2, 'earth', 'gam', 'noun', 'test');
"""

#: Deterministic n-gram tables so ``/predictions/*`` never needs the real DB.
_TINY_TABLES: dict = {
    "unigrams": {"khi": 100, "le": 200},
    "bigrams": {("khi", "a"): 50, ("khi", "b"): 30, ("le", "a"): 100},
}


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Warn auth, default engine mode, clean key cache / rate buckets."""
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    monkeypatch.delenv(ENGINE_MODE_ENV, raising=False)
    for var in AI_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "engine_mode", DEFAULT_ENGINE_MODE)
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def seed_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seeded throwaway lexicon DB wired into ``config.paths.db``."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.paths, "db", db_path)
    return db_path


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Main app (no lifespan) with hermetic n-gram tables."""
    monkeypatch.setattr(
        "zolai.api.prediction_api.load_ngram_tables", lambda: dict(_TINY_TABLES)
    )
    return TestClient(create_app())


# ── Parametrized status + shape smoke ───────────────────────────────────────

_CASES = [
    # core#16: public health must not leak ``data_root`` (the data path).
    pytest.param("/health", {}, {"status", "version", "uptime_s"}, id="health"),
    # core#22: the Bible probe reports the rows it can actually serve.
    pytest.param(
        "/bible/status",
        {},
        {"status", "bible_files", "verses", "available", "path"},
        id="bible-status",
    ),
    pytest.param(
        "/api/v1/lexicon/pasian",
        {},
        {"word", "found", "zo_en", "en_zo"},
        id="lexicon-hit",
    ),
    pytest.param(
        "/api/v1/lexicon/zzz-not-a-word",
        {},
        {"word", "found", "zo_en", "en_zo"},
        id="lexicon-miss",
    ),
    pytest.param(
        "/api/v1/linguistics/pos",
        {"text": "Pasian in gam a piangsak hi."},
        {"text", "tokens"},
        id="linguistics-pos",
    ),
    pytest.param(
        "/api/v1/predictions/health",
        {},
        {"status", "tables_loaded", "unigram_count", "bigram_count", "mode"},
        id="predictions-health",
    ),
    pytest.param(
        "/api/v1/predictions/next",
        {"word": "khi"},
        {"word", "predictions"},
        id="predictions-next",
    ),
    pytest.param(
        "/api/v1/records",
        {"table": "dictionary"},
        {"table", "items", "next_cursor", "has_more"},
        id="records",
    ),
]


@pytest.mark.parametrize(("path", "params", "keys"), _CASES)
def test_public_get_smoke(
    path: str, params: dict, keys: set[str], client: TestClient, seed_db: Path
) -> None:
    resp = client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert keys <= set(body), f"{path}: missing keys {sorted(keys - set(body))}"


# ── Deeper shape assertions ─────────────────────────────────────────────────


def test_monitor_health_never_leaks_the_data_path(client: TestClient, seed_db: Path) -> None:
    """core#24: ``/monitor/health`` used to answer ``sqlite:////home/…/zolai.db``.

    The route is public, so the full path *is* ``data_root`` by another name.
    """
    resp = client.get("/monitor/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data_root" not in body
    assert "db_path" not in body
    tail = body["db_url"].split(":///", 1)[-1]
    assert "/" not in tail and "\\" not in tail, body["db_url"]
    assert str(seed_db.parent) not in body["db_url"]


def test_monitor_health_is_offloaded_from_the_event_loop(
    client: TestClient, seed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core#24: ``db.health_check()`` UNION-scans every table (~17s on the prod
    corpus) and ran inline in an ``async def`` — one monitor call froze *all*
    in-flight requests. The handler must run it off the event loop."""
    from zolai.data.database import DatabaseManager

    seen: dict[str, bool] = {}

    def fake_health_check(self, *, force: bool = False):  # noqa: ANN001
        try:
            asyncio.get_running_loop()
            seen["inline"] = True  # still on the loop thread — the old bug
        except RuntimeError:
            seen["inline"] = False  # a worker thread: no loop, no freeze
        return {"db_url": "sqlite:///fake.db", "backend": "sqlite"}

    monkeypatch.setattr(DatabaseManager, "health_check", fake_health_check)
    resp = client.get("/monitor/health")
    assert resp.status_code == 200, resp.text
    assert seen.get("inline") is False, "health check ran on the event loop"


def test_monitor_health_answers_from_the_ttl_cache(
    client: TestClient, seed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 30s poll must not re-scan the DB every time: within the TTL the
    snapshot is reused, and ``force=True`` still reads fresh (core#24)."""
    from zolai.data.database import DatabaseManager, get_manager

    calls: list[bool] = []
    real_counts = DatabaseManager.counts

    def counting_counts(self, *, force: bool = False):
        calls.append(True)
        return real_counts(self, force=force)

    monkeypatch.setattr(DatabaseManager, "counts", counting_counts)
    mgr = get_manager()
    mgr._health_cache = None  # drop any snapshot an earlier test cached

    first = client.get("/monitor/health")
    assert first.status_code == 200, first.text
    second = client.get("/monitor/health")
    assert second.status_code == 200, second.text
    assert len(calls) == 1, f"expected one scan inside the TTL, got {len(calls)}"

    assert mgr.health_check(force=True)["table_counts"]
    assert len(calls) == 2, "force=True must bypass the cache"


def test_legacy_stats_falls_back_to_db_counts(
    client: TestClient, seed_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core#9 / core#7: the JSONL auto-discovery legitimately finds nothing in
    production (the corpus lives in SQLite). ``/stats`` answered 503 there —
    it must report DB-backed counts instead while the store is healthy."""
    from zolai.analyzer.corpus import CorpusAnalyzer

    monkeypatch.setattr(
        CorpusAnalyzer, "full_stats", lambda self: {"error": "No data loaded"}
    )
    resp = client.get("/stats")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "sqlite_knowledge"
    assert body["note"] == "No data loaded"
    assert body["table_counts"]["dictionary"] >= 1
    assert body["total_rows"] == sum(body["table_counts"].values())


def test_lexicon_hit_and_miss_contract(client: TestClient, seed_db: Path) -> None:
    hit = client.get("/api/v1/lexicon/pasian")
    assert hit.status_code == 200
    hit_body = hit.json()
    assert hit_body["found"] is True
    assert hit_body["word"] == "pasian"
    assert hit_body["zo_en"][0]["zolai"] == "pasian"
    assert "Cache-Control" in hit.headers

    miss = client.get("/api/v1/lexicon/zzz-not-a-word")
    assert miss.status_code == 200
    assert miss.json()["found"] is False


def test_linguistics_pos_token_shape(client: TestClient, seed_db: Path) -> None:
    resp = client.get("/api/v1/linguistics/pos", params={"text": "Pasian in gam a piangsak hi."})
    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    assert tokens, "tagger returned no tokens"
    for token in tokens:
        assert set(token) == {"word", "pos"}
        assert isinstance(token["word"], str) and isinstance(token["pos"], str)


def test_predictions_health_reports_default_rule_mode(
    client: TestClient, seed_db: Path
) -> None:
    resp = client.get("/api/v1/predictions/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == DEFAULT_ENGINE_MODE
    assert body["tables_loaded"] is True
    assert body["status"] == "ok"


def test_predictions_next_shape(client: TestClient, seed_db: Path) -> None:
    resp = client.get("/api/v1/predictions/next", params={"word": "khi", "top_k": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["word"] == "khi"
    assert body["predictions"], "expected backoff predictions from tiny tables"
    for pred in body["predictions"]:
        assert set(pred) == {"next", "count"}
        assert isinstance(pred["next"], str) and isinstance(pred["count"], int)


def test_records_read_only_shape(client: TestClient, seed_db: Path) -> None:
    resp = client.get("/api/v1/records", params={"table": "dictionary"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["table"] == "dictionary"
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["id"] >= 1


def test_r17_observation_pipeline_router_not_mounted(client: TestClient) -> None:
    """R17 (Phase 2 §36): the observation/pipeline surface stays engine/CLI-only.

    The observation engine writes derived tables, so it must never be reachable
    over HTTP without an audit path — no ``/observation*`` or ``/pipeline*``
    route may be mounted on the API server (Phase 6 decides HTTP exposure).
    """
    paths = {getattr(route, "path", "") for route in client.app.routes}
    offenders = sorted(p for p in paths if "observation" in p or "pipeline" in p)
    assert offenders == [], f"observation/pipeline routes mounted: {offenders}"

