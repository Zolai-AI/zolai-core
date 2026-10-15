"""HTTP surface for the five learning tools (websocket, consensus, metrics,
corpus, finetuning).

Every test binds a throwaway SQLite engine to ``app.state.learning_engine``, so
the production 2.4 GB database is never opened and every write path — the
corpus expansion, the metric samples, the training dataset, the model registry
— lands in a temp file.

Two invariants are pinned here:

* **RBAC classification** — public reads and computations answer anonymous
  callers under ``enforce``; anything that writes is a strict 401.
* **DB only** — no endpoint writes a ``.json`` / ``.jsonl`` artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from starlette.websockets import WebSocketDisconnect

from zolai.api import rbac
from zolai.api.server import app

PREFIX = "/api/v1/learning"

BIBLE_SCHEMA = """
CREATE TABLE bible_verses (
    ref TEXT,
    book TEXT,
    chapter INTEGER,
    verse INTEGER,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    en_kJV TEXT
)
"""

WORD_ALIGNMENT_SCHEMA = """
CREATE TABLE word_alignments (
    id INTEGER PRIMARY KEY,
    ref TEXT,
    zolai_word TEXT,
    english_word TEXT,
    position INTEGER
)
"""

SIGNALS_SCHEMA = """
CREATE TABLE learning_signals (
    signal_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT,
    query TEXT NOT NULL DEFAULT '',
    response TEXT NOT NULL DEFAULT '',
    citations TEXT NOT NULL DEFAULT '[]',
    retrieval_only INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    engine_mode TEXT NOT NULL DEFAULT '',
    feedback_type TEXT NOT NULL DEFAULT '',
    feedback_value TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
)
"""

REVIEW_QUEUE_SCHEMA = """
CREATE TABLE learning_review_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT
)
"""

DICTIONARY_SCHEMA = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT,
    english TEXT,
    pos TEXT
)
"""

VERSES = [
    ("GEN 1:1", "GEN", 1, 1, "A kipat cil-in Pasian in vantung leh leitung a piangsak hi.",
     "A kipat...", "In the beginning God created the heaven and the earth."),
    ("GEN 1:2", "GEN", 1, 2, "Cil-in vantung hi, mikhat no hiam.",
     None, "And the earth was without form, and void."),
    ("MAT 1:1", "MAT", 1, 1, "Cilhngiah ngei Jesus Christ ah mi maw.",
     None, "The book of the genealogy of Jesus Christ."),
]


@pytest.fixture()
def engine(tmp_path: Path):
    """Throwaway SQLite engine holding only the corpus/learning tables."""
    eng = create_engine(f"sqlite:///{tmp_path / 'learning_tools.db'}")
    with eng.begin() as conn:
        for schema in (BIBLE_SCHEMA, DICTIONARY_SCHEMA, WORD_ALIGNMENT_SCHEMA,
                       SIGNALS_SCHEMA, REVIEW_QUEUE_SCHEMA):
            conn.execute(text(schema))
        for row in VERSES:
            conn.execute(
                text("""
                    INSERT INTO bible_verses
                        (ref, book, chapter, verse, zo_tdb77, zo_tedim2010, en_kJV)
                    VALUES (:ref, :book, :chapter, :verse, :zo, :tedim, :en)
                """),
                {"ref": row[0], "book": row[1], "chapter": row[2], "verse": row[3],
                 "zo": row[4], "tedim": row[5], "en": row[6]},
            )
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    """Point the whole learning-tools surface at the throwaway engine."""
    app.state.learning_engine = engine
    try:
        yield engine
    finally:
        app.state.learning_engine = None


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with auth disabled (the functional tests)."""
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    return TestClient(app)


@pytest.fixture()
def seeded_signals(db):
    """Six approved feedback signals the fine-tuning pipeline can train on."""
    with db.begin() as conn:
        for i in range(6):
            conn.execute(
                text("""
                    INSERT INTO learning_signals
                        (signal_id, query, response, feedback_type, feedback_value,
                         confidence, status, created_at, updated_at)
                    VALUES (:sid, :q, :r, :ft, :fv, :conf, :st, '', '')
                """),
                {"sid": f"sig_{i}", "q": f"Translate word {i}",
                 "r": f"Word {i} means thing", "ft": "correction",
                 "fv": f"Word {i} means thing (corrected)", "conf": 0.9,
                 "st": "approved"},
            )
            conn.execute(
                text(
                    "INSERT INTO learning_review_queue (signal_id, priority, status, "
                    "created_at) VALUES (:sid, 0, :st, '')"
                ),
                {"sid": f"sig_{i}", "st": "approved"},
            )


# ── Consensus ───────────────────────────────────────────────────────────────


class TestConsensus:
    def test_vote_types(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/consensus/vote-types")
        assert r.status_code == 200, r.text
        assert r.json()["vote_types"] == ["agree", "disagree", "abstain", "conditional"]

    def test_build_reaches_consensus(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/consensus/build", json={
            "topic": "Is 'in' the ergative marker?",
            "votes": [
                {"agent_id": "a1", "agent_role": "grammar_expert",
                 "vote_type": "agree", "confidence": 0.9},
                {"agent_id": "a2", "agent_role": "bible_learner",
                 "vote_type": "agree", "confidence": 0.8},
                {"agent_id": "a3", "agent_role": "translator",
                 "vote_type": "disagree", "confidence": 0.4,
                 "reasoning": "it is also a locative"},
            ],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["votes_counted"] == 3
        assert body["consensus"]["consensus_reached"] is True
        assert body["consensus"]["winning_position"] == "agree"
        assert body["consensus"]["minority_opinions"] == ["a3: it is also a locative"]

    def test_build_with_no_votes(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/consensus/build", json={"topic": "empty"})
        assert r.status_code == 200, r.text
        assert r.json()["consensus"]["reason"] == "no_votes"

    def test_build_rejects_an_unknown_vote_confidence(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/consensus/build", json={
            "topic": "bad",
            "votes": [{"agent_id": "a1", "confidence": 4.0}],
        })
        assert r.status_code == 422

    def test_analyze_transcript(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/consensus/analyze", json={"messages": [
            {"from_agent": "a1", "content": "I agree, this is the ergative case."},
            {"from_agent": "a2", "content": "I dispute it; the case marker is postpositional."},
            {"from_agent": "a1", "content": "We should add an example, therefore."},
        ]})
        assert r.status_code == 200, r.text
        body = r.json()["insights"]
        assert body["participant_count"] == 2
        assert body["total_messages"] == 3
        assert [a["agent"] for a in body["agreements"]] == ["a1"]
        assert [d["agent"] for d in body["disagreements"]] == ["a2"]

    def test_analyze_action_items(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/consensus/analyze", json={"messages": [
            {"from_agent": "a1", "content": "We should add an example, therefore."},
        ]})
        assert r.status_code == 200, r.text
        assert r.json()["action_items"]

    def test_weights_from_decisions(self, client: TestClient) -> None:
        decisions = [
            {"agent_id": "good", "agent_role": "grammar_expert", "was_correct": True,
             "confidence": 0.95, "topic": "grammar"},
            {"agent_id": "bad", "agent_role": "translator", "was_correct": False,
             "confidence": 0.2, "topic": "grammar"},
        ] * 8
        r = client.post(f"{PREFIX}/consensus/weights", json={"decisions": decisions})
        assert r.status_code == 200, r.text
        weights = r.json()["weights"]
        assert weights["good"] > weights["bad"]


# ── Metrics ─────────────────────────────────────────────────────────────────


class TestMetrics:
    def test_dashboard_and_names_on_an_empty_db(self, client: TestClient, db) -> None:
        r = client.get(f"{PREFIX}/metrics")
        assert r.status_code == 200, r.text
        assert r.json()["metrics"] == {}

        r = client.get(f"{PREFIX}/metrics/names")
        assert r.status_code == 200, r.text
        assert r.json()["names"] == []

    def test_bad_hours_is_rejected(self, client: TestClient, db) -> None:
        assert client.get(f"{PREFIX}/metrics?hours=0").status_code == 400
        assert client.get(f"{PREFIX}/metrics?hours=9999").status_code == 400

    def test_collect_is_admin_only(self, monkeypatch: pytest.MonkeyPatch, db) -> None:
        """The collect pass writes ``learning_metrics`` rows — never public."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        r = TestClient(app).post(f"{PREFIX}/metrics/collect")
        assert r.status_code == 401, r.text

    def test_collect_then_read(self, client: TestClient, db) -> None:
        before = client.get(f"{PREFIX}/metrics/names").json()["names"]
        r = client.post(f"{PREFIX}/metrics/collect")
        assert r.status_code == 200, r.text
        assert "system" in r.json()["collected"]

        after = client.get(f"{PREFIX}/metrics/names").json()["names"]
        assert set(before) <= set(after)
        assert any(n.startswith("system.") for n in after)

        sample = [n for n in after if n.startswith("system.")][0]
        r = client.get(f"{PREFIX}/metrics/stream/{sample}")
        assert r.status_code == 200, r.text
        assert r.json()["samples"]
        assert r.json()["stats"]["count"] >= 1

    def test_export_formats_render_without_touching_disk(
        self, client: TestClient, db
    ) -> None:
        client.post(f"{PREFIX}/metrics/collect")
        expected_types = {
            "prometheus": "text/plain; version=0.0.4",
            "csv": "text/csv",
            "influxdb": "text/plain",
            "json": "application/json",
            "grafana": "application/json",
        }
        for fmt, media_type in expected_types.items():
            r = client.get(f"{PREFIX}/metrics/export/{fmt}")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith(media_type), fmt

        r = client.get(f"{PREFIX}/metrics/export/csv")
        assert r.text.splitlines()[0] == "metric_name,timestamp,value,labels,source"
        assert "timestamp" in json.loads(
            client.get(f"{PREFIX}/metrics/export/json").text
        )

        r = client.get(f"{PREFIX}/metrics/export/grafana")
        assert json.loads(r.text)["dashboard"]["title"].startswith("Zolai")

    def test_export_rejects_an_unknown_format(self, client: TestClient, db) -> None:
        r = client.get(f"{PREFIX}/metrics/export/parquet")
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "unknown_format"

    def test_influx_push_is_admin_only(self, monkeypatch: pytest.MonkeyPatch, db) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        r = TestClient(app).post(f"{PREFIX}/metrics/export/influxdb",
                                 json={"hours": 24, "url": "http://127.0.0.1:1"})
        assert r.status_code == 401, r.text


# ── Corpus ──────────────────────────────────────────────────────────────────


class TestCorpus:
    def test_health_and_stats(self, client: TestClient, db) -> None:
        r = client.get(f"{PREFIX}/corpus/health")
        assert r.status_code == 200, r.text
        health = r.json()
        assert health["total_verses"] == len(VERSES)
        assert health["complete_pairs"] == len(VERSES)
        assert health["status"] == "healthy"

        r = client.get(f"{PREFIX}/corpus/stats")
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["stats"]["total_verses"] == len(VERSES)
        assert stats["stats"]["stored_pairs"] == 0

    def test_quick_check(self, client: TestClient, db) -> None:
        r = client.post(f"{PREFIX}/corpus/check",
                        json={"refs": ["GEN 1:1", "NOPE 9:9"]})
        assert r.status_code == 200, r.text
        checks = r.json()["checks"]
        assert checks[0]["valid"] is True
        assert checks[0]["has_tedim"] is True
        assert checks[1] == {"valid": False, "error": "Verse not found"}

    def test_expand_is_admin_only(self, monkeypatch: pytest.MonkeyPatch, db) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        r = TestClient(app).post(f"{PREFIX}/corpus/expand", json={})
        assert r.status_code == 401, r.text

    def test_expand_writes_rows_and_is_idempotent(self, client: TestClient, db) -> None:
        first = client.post(f"{PREFIX}/corpus/expand", json={})
        assert first.status_code == 200, first.text
        run = first.json()["run"]
        assert run["parallel_pairs"] == len(VERSES)
        # Two chapters (GEN 1, MAT 1) covering every seeded verse.
        assert run["chapters"] == 2
        assert run["chapter_expansions"] == len(VERSES)

        stats = client.get(f"{PREFIX}/corpus/stats").json()
        assert stats["stats"]["stored_pairs"] == run["stored_pairs"]

        second = client.post(f"{PREFIX}/corpus/expand", json={})
        assert second.status_code == 200, second.text
        after = client.get(f"{PREFIX}/corpus/stats").json()
        assert after["stats"]["stored_pairs"] == stats["stats"]["stored_pairs"]
        assert after["stored_expansions"] == stats["stored_expansions"]

    def test_validate_is_admin_only(self, monkeypatch: pytest.MonkeyPatch, db) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        r = TestClient(app).post(f"{PREFIX}/corpus/validate", json={})
        assert r.status_code == 401, r.text

    def test_validate_persists_a_readable_report(self, client: TestClient, db) -> None:
        r = client.post(f"{PREFIX}/corpus/validate", json={"zvs_limit": 10})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report"]["summary"]["total_checked"] == len(VERSES)
        report_id = body["report_id"]

        r = client.get(f"{PREFIX}/corpus/reports/{report_id}")
        assert r.status_code == 200, r.text
        stored = r.json()
        assert stored["report_id"] == report_id
        assert stored["issue_count"] == len(body["report"]["issues"])

        assert client.get(f"{PREFIX}/corpus/reports/qr_nope").status_code == 404

    def test_validate_without_saving(self, client: TestClient, db) -> None:
        r = client.post(f"{PREFIX}/corpus/validate", json={"save": False})
        assert r.status_code == 200, r.text
        assert "report_id" not in r.json()


# ── Finetuning ──────────────────────────────────────────────────────────────


class TestFinetuning:
    def test_every_finetuning_route_is_admin(
        self, monkeypatch: pytest.MonkeyPatch, db
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        for method, path in (
            ("GET", "/finetuning/models"),
            ("GET", "/finetuning/models/active"),
            ("GET", "/finetuning/models/m1"),
            ("GET", "/finetuning/jobs"),
            ("GET", "/finetuning/jobs/j1"),
            ("GET", "/finetuning/datasets/ds_1/items"),
            ("POST", "/finetuning/models"),
            ("POST", "/finetuning/jobs"),
            ("POST", "/finetuning/jobs/j1/cancel"),
            ("POST", "/finetuning/models/m1/activate"),
            ("PATCH", "/finetuning/models/m1"),
            ("DELETE", "/finetuning/models/m1"),
        ):
            assert rbac.classify_route(method, f"{PREFIX}{path}") == "admin", path
            assert c.request(method, f"{PREFIX}{path}", json={}).status_code == 401, path

    def test_model_registry_round_trip(self, client: TestClient, db) -> None:
        r = client.post(f"{PREFIX}/finetuning/models", json={
            "name": "lora_model", "version": "v1", "base_model": "base/one",
            "training_method": "lora", "metrics": {"loss": 0.1},
        })
        assert r.status_code == 201, r.text
        model_id = r.json()["model_id"]

        r = client.get(f"{PREFIX}/finetuning/models")
        assert r.status_code == 200, r.text
        assert [m["model_id"] for m in r.json()["models"]] == [model_id]

        # The first registered model becomes the active one.
        r = client.get(f"{PREFIX}/finetuning/models/active")
        assert r.status_code == 200, r.text
        assert r.json()["model_id"] == model_id

        r = client.patch(f"{PREFIX}/finetuning/models/{model_id}",
                         json={"status": "checkpoint"})
        assert r.status_code == 200, r.text
        assert client.get(
            f"{PREFIX}/finetuning/models/{model_id}"
        ).json()["status"] == "checkpoint"

        r = client.delete(f"{PREFIX}/finetuning/models/{model_id}?force=true")
        assert r.status_code == 200, r.text
        assert client.get(f"{PREFIX}/finetuning/models/{model_id}").status_code == 404

    def test_delete_active_model_needs_force(self, client: TestClient, db) -> None:
        model_id = client.post(f"{PREFIX}/finetuning/models", json={
            "name": "m", "version": "v1", "base_model": "b",
        }).json()["model_id"]
        r = client.delete(f"{PREFIX}/finetuning/models/{model_id}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "model_conflict"

    def test_unknown_model_is_404(self, client: TestClient, db) -> None:
        assert client.get(f"{PREFIX}/finetuning/models/nope").status_code == 404
        assert client.post(f"{PREFIX}/finetuning/models/nope/activate").status_code == 404
        assert client.get(f"{PREFIX}/finetuning/jobs/nope").status_code == 404

    def test_no_models_yet(self, client: TestClient, db) -> None:
        assert client.get(f"{PREFIX}/finetuning/models/active").status_code == 404
        assert client.get(f"{PREFIX}/finetuning/jobs").json() == {"jobs": []}

    def test_create_training_job_builds_a_dataset(
        self, client: TestClient, seeded_signals, db
    ) -> None:
        r = client.post(f"{PREFIX}/finetuning/jobs", json={
            "method": "lora", "max_steps": 5, "min_confidence": 0.5,
            "status_filter": "approved", "wait_seconds": 5.0,
        })
        assert r.status_code == 201, r.text
        body = r.json()
        job_id = body["job_id"]
        dataset_id = body["dataset_id"]
        assert dataset_id.startswith("ds_")

        status = client.get(f"{PREFIX}/finetuning/jobs/{job_id}").json()
        assert status["status"] == "completed"
        assert status["progress"] == pytest.approx(1.0)
        assert status["model_id"]

        r = client.get(f"{PREFIX}/finetuning/datasets/{dataset_id}/items")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 6

        # The completed job registered its model in the registry.
        registered = client.get(f"{PREFIX}/finetuning/models").json()["models"]
        assert [m["model_id"] for m in registered] == [status["model_id"]]

    def test_create_training_job_without_signals_is_409(
        self, client: TestClient, db
    ) -> None:
        r = client.post(f"{PREFIX}/finetuning/jobs", json={})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "no_feedback_signals"

    def test_create_training_job_rejects_an_unknown_method(
        self, client: TestClient, seeded_signals, db
    ) -> None:
        r = client.post(f"{PREFIX}/finetuning/jobs", json={"method": "magic"})
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["error"] == "unknown_method"

    def test_cancel_unknown_job_is_409(self, client: TestClient, db) -> None:
        r = client.post(f"{PREFIX}/finetuning/jobs/nope/cancel")
        assert r.status_code == 409, r.text


# ── RBAC classification ─────────────────────────────────────────────────────

PUBLIC_ROUTES = [
    ("GET", "/consensus/vote-types"),
    ("POST", "/consensus/build"),
    ("POST", "/consensus/analyze"),
    ("POST", "/consensus/weights"),
    ("GET", "/metrics"),
    ("GET", "/metrics/names"),
    ("GET", "/metrics/export/{fmt}"),
    ("GET", "/corpus/health"),
    ("GET", "/corpus/stats"),
    ("GET", "/corpus/reports/{report_id}"),
    ("POST", "/corpus/check"),
]

ADMIN_ROUTES = [
    ("POST", "/metrics/collect"),
    ("POST", "/metrics/export/influxdb"),
    ("POST", "/corpus/expand"),
    ("POST", "/corpus/validate"),
    ("GET", "/finetuning/models"),
    ("POST", "/finetuning/models"),
    ("PATCH", "/finetuning/models/{model_id}"),
    ("DELETE", "/finetuning/models/{model_id}"),
    ("POST", "/finetuning/jobs"),
]


class TestRbacClassification:
    @pytest.mark.parametrize("method,path", PUBLIC_ROUTES)
    def test_public_routes_are_public(self, method: str, path: str) -> None:
        assert rbac.is_public_path(method, f"{PREFIX}{path}"), path
        assert rbac.classify_route(method, f"{PREFIX}{path}") == "public", path

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_admin_routes_are_admin(self, method: str, path: str) -> None:
        assert rbac.is_admin_path(f"{PREFIX}{path}", method), path
        assert rbac.classify_route(method, f"{PREFIX}{path}") == "admin", path
        assert not rbac.is_public_path(method, f"{PREFIX}{path}"), path

    def test_the_websocket_path_is_admin_classified(self) -> None:
        """Not an HTTP method, but the class must still say admin."""
        assert "/api/v1/learning/ws" in rbac.ADMIN_PREFIXES
        assert rbac.classify_route("GET", f"{PREFIX}/ws") == "admin"

    def test_agent_surface_entries_are_intact(self) -> None:
        for entry in (
            ("POST", "/api/v1/learning/agents"),
            ("PATCH", "/api/v1/learning/agents"),
            ("DELETE", "/api/v1/learning/agents"),
            ("POST", "/api/v1/learning/skills"),
            ("POST", "/api/v1/learning/bindings"),
            ("DELETE", "/api/v1/learning/bindings"),
        ):
            assert rbac.is_admin_path(entry[1], entry[0]), entry

    def test_every_new_route_is_classified(self) -> None:
        """The completeness guard, scoped to the tools surface."""
        spec = app.openapi()
        tools = [p for p in spec["paths"] if p.startswith(f"{PREFIX}/")]
        prefixes = ("/consensus", "/metrics", "/corpus", "/finetuning")
        unclassified = []
        for path, operations in spec["paths"].items():
            if not any(path.startswith(f"{PREFIX}{pre}") for pre in prefixes):
                continue
            for method in operations:
                if rbac.classify_route(method.upper(), path) == "unclassified":
                    unclassified.append(f"{method.upper()} {path}")
        assert unclassified == []
        assert len(tools) >= 30


class TestAnonymousUnderEnforce:
    """Public reads/computations answer anonymously; writes 401."""

    #: Only the POST routes need a body.
    BODIES = {
        "/consensus/build": {"topic": "ergative case", "votes": []},
        "/consensus/analyze": {"messages": []},
        "/consensus/weights": {"decisions": []},
        "/corpus/check": {"refs": ["GEN 1:1"]},
    }

    @pytest.mark.parametrize("method,path", [
        ("GET", "/consensus/vote-types"),
        ("POST", "/consensus/build"),
        ("POST", "/consensus/analyze"),
        ("POST", "/consensus/weights"),
        ("GET", "/metrics"),
        ("GET", "/metrics/names"),
        ("GET", "/metrics/export/csv"),
        ("GET", "/corpus/health"),
        ("GET", "/corpus/stats"),
        ("POST", "/corpus/check"),
    ])
    def test_public_routes_answer_anonymous(
        self, monkeypatch: pytest.MonkeyPatch, db, method: str, path: str
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.request(method, f"{PREFIX}{path}",
                      json=self.BODIES.get(path) if method == "POST" else None)
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_admin_routes_401_anonymous(
        self, monkeypatch: pytest.MonkeyPatch, db, method: str, path: str
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        r = TestClient(app).request(method, f"{PREFIX}{path}", json={})
        assert r.status_code == 401, r.text


# ── WebSocket ───────────────────────────────────────────────────────────────


class TestLearningWebSocket:
    def test_open_when_auth_is_off(self, client: TestClient, db) -> None:
        with client.websocket_connect(f"{PREFIX}/ws") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "welcome"
            assert welcome["payload"]["connection_id"]

            ws.send_json({"type": "ping", "payload": {}})
            assert ws.receive_json()["type"] == "pong"

    def test_topics_and_metrics(self, client: TestClient, db) -> None:
        with client.websocket_connect(f"{PREFIX}/ws") as ws:
            ws.receive_json()  # welcome

            ws.send_json({"type": "subscribe", "payload": {"topics": ["metrics"]}})
            assert ws.receive_json()["payload"]["topics"] == ["metrics"]

            ws.send_json({"type": "get_system_status", "payload": {}})
            frame = ws.receive_json()
            assert frame["type"] == "system_status"
            assert frame["payload"]["agents_total"] >= 4

    def test_discussion_round_trip_is_persisted(self, client: TestClient, db) -> None:
        with client.websocket_connect(f"{PREFIX}/ws") as ws:
            ws.receive_json()  # welcome
            ws.send_json({
                "type": "start_discussion", "sender": "a1",
                "payload": {"topic": "ergative case", "initial_message": "I agree."},
            })
            started = ws.receive_json()
            assert started["type"] == "discussion_started"
            discussion_id = started["payload"]["discussion_id"]

            ws.send_json({
                "type": "discussion_message", "sender": "a2",
                "payload": {"discussion_id": discussion_id, "content": "I agree."},
            })
            ack = ws.receive_json()
            assert ack["type"] == "discussion_message_ack"

            ws.send_json({"type": "get_discussion_status",
                          "payload": {"discussion_id": discussion_id}})
            status = ws.receive_json()
            # One from ``initial_message``, one from the explicit frame above.
            assert status["payload"]["message_count"] == 2

        from sqlalchemy import text as sql_text

        with db.connect() as conn:
            rows = conn.execute(sql_text(
                "SELECT discussion_id FROM agent_discussions WHERE discussion_id = :d"
            ), {"d": discussion_id}).fetchall()
        assert len(rows) == 1

    def test_task_round_trip(self, client: TestClient, db) -> None:
        with client.websocket_connect(f"{PREFIX}/ws") as ws:
            ws.receive_json()  # welcome
            ws.send_json({"type": "submit_task", "sender": "a1",
                          "payload": {"type": "translate", "payload": {"text": "hi"}}})
            submitted = ws.receive_json()
            assert submitted["type"] == "task_submitted"
            task_id = submitted["payload"]["task_id"]

            ws.send_json({"type": "get_task_status", "payload": {"task_id": task_id}})
            assert ws.receive_json()["payload"]["status"] == "pending"

            ws.send_json({"type": "cancel_task", "payload": {"task_id": task_id}})
            assert ws.receive_json()["payload"]["status"] == "cancelled"

    def test_mutation_over_the_socket_is_admin_only(
        self, monkeypatch: pytest.MonkeyPatch, db
    ) -> None:
        """The socket is gated: anonymous cannot even complete a handshake."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        with pytest.raises(WebSocketDisconnect) as exc:
            with TestClient(app).websocket_connect(f"{PREFIX}/ws"):
                pass
        assert exc.value.code == 4401


# ── DB-only guarantee ───────────────────────────────────────────────────────


class TestNoFileArtifacts:
    def test_write_paths_create_no_json_or_jsonl(
        self, client: TestClient, seeded_signals, tmp_path: Path
    ) -> None:
        """Exercise every writer and prove nothing landed on disk.

        The corpus expansion, the metric collection pass, a validation run, a
        model registration and a training job all used to be file-backed. With
        the temp SQLite engine bound to the surface, a stray ``.json`` /
        ``.jsonl`` write would surface under ``tmp_path``.
        """
        workdir = tmp_path / "cwd"
        workdir.mkdir()
        cwd_before = Path.cwd()
        import os

        os.chdir(workdir)
        try:
            assert client.post(f"{PREFIX}/corpus/expand",
                               json={}).status_code == 200
            assert client.post(f"{PREFIX}/metrics/collect").status_code == 200
            assert client.post(f"{PREFIX}/corpus/validate", json={}).status_code == 200
            assert client.post(f"{PREFIX}/finetuning/models", json={
                "name": "m", "version": "v1", "base_model": "b",
            }).status_code == 201
            assert client.post(f"{PREFIX}/finetuning/jobs", json={
                "max_steps": 2, "min_confidence": 0.5, "status_filter": "approved",
            }).status_code == 201
        finally:
            os.chdir(cwd_before)

        artifacts = [str(p.relative_to(workdir))
                     for p in workdir.rglob("*")
                     if p.is_file() and p.suffix in (".json", ".jsonl")]
        assert artifacts == []

        # …and the data really is in SQLite, not on disk.
        from sqlalchemy import text as sql_text

        engine = app.state.learning_engine
        with engine.connect() as conn:
            for table in ("corpus_chapter_expansions", "corpus_parallel_pairs",
                          "learning_metrics", "corpus_quality_reports",
                          "finetune_model_registry", "finetune_dataset_items"):
                assert conn.execute(
                    sql_text(f"SELECT COUNT(*) FROM {table}")
                ).scalar() > 0, table