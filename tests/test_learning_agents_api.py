"""HTTP surface for the DB-driven multi-agent learning system.

The agent registry used to be reachable only from Python. These tests pin the
mounted routes: they must answer, they must classify as admin, and they must
reject anonymous callers under ``enforce``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from zolai.api import rbac
from zolai.api.server import app

PREFIX = "/api/v1/learning"


@pytest.fixture(autouse=True)
def scratch_corpus(scratch_db, monkeypatch: pytest.MonkeyPatch):
    """Every route here writes, so nothing here may reach the corpus.

    ``POST /run`` creates ``learning_signals`` and inserts a task row; ``GET
    /overview`` seeds the agent registry. This file used to redirect no database at
    all, so all of that landed in the live ``data/zolai.db`` — that is where the
    polluted ``hypotheses``/``data_audit_log`` rows came from, and the write fence
    only noticed at teardown, after the rows were already in the corpus. The tests
    then "passed" by reading real corpus rows, so pinning them to a scratch DB
    surfaced a second problem: they had no schema. Create it, and seed the handful
    of verses the learning agents query.
    """
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from zolai.data.models import Base, BibleVerse

    engine = create_engine(f"sqlite:///{scratch_db}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                BibleVerse(
                    ref="Gen.1.1", book="Genesis", chapter=1, verse=1,
                    zo_tdb77="ṥin hnuei hlauhna Tinte a thlen salh",
                    en_kJV="In the beginning God created the heaven and the earth.",
                ),
                BibleVerse(
                    ref="John.3.16", book="John", chapter=3, verse=16,
                    zo_tdb77="Tinte hnuai a hi humphat a hnain, Fapa thak a siam",
                    en_kJV="For God so loved the world, that he gave his only begotten Son,",
                ),
            ]
        )
        session.commit()
    engine.dispose()
    return scratch_db


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with auth disabled.

    The suite runs with ``ZOLAI_API_AUTH=enforce`` by default, which is what
    the 401 assertions below rely on. Functional tests opt out explicitly.
    """
    monkeypatch.setenv("ZOLAI_API_AUTH", "off")
    return TestClient(app)


class TestManagementRoutes:
    def test_overview(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/overview")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["agents_total"] >= 4
        assert body["skills_total"] > 0

    def test_list_and_get_agent(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/agents")
        assert r.status_code == 200, r.text
        ids = {a["agent_id"] for a in r.json()["agents"]}
        assert {"bible_learner_001", "translator_001",
                "grammar_expert_001", "vocabulary_builder_001"} <= ids

        r = client.get(f"{PREFIX}/agents/translator_001")
        assert r.status_code == 200
        assert r.json()["role"] == "translator"

    def test_unknown_agent_is_404(self, client: TestClient) -> None:
        assert client.get(f"{PREFIX}/agents/does_not_exist").status_code == 404

    def test_list_skills(self, client: TestClient) -> None:
        r = client.get(f"{PREFIX}/skills")
        assert r.status_code == 200, r.text
        assert r.json()["skills"]

    def test_tuning_round_trip(self, client: TestClient) -> None:
        """A tuning change through the API must persist to the DB."""
        original = client.get(f"{PREFIX}/agents/grammar_expert_001").json()
        before = original.get("config", {}).get("confidence_threshold", 0.9)

        r = client.patch(
            f"{PREFIX}/agents/grammar_expert_001",
            json={"confidence_threshold": 0.66},
        )
        assert r.status_code == 200, r.text
        assert client.get(
            f"{PREFIX}/agents/grammar_expert_001"
        ).json()["config"]["confidence_threshold"] == pytest.approx(0.66)

        # restore
        client.patch(
            f"{PREFIX}/agents/grammar_expert_001",
            json={"confidence_threshold": before},
        )

    def test_disable_then_enable(self, client: TestClient) -> None:
        assert client.post(
            f"{PREFIX}/agents/grammar_expert_001/disable"
        ).status_code == 200
        assert client.get(f"{PREFIX}/overview").json()["agents_disabled"] >= 1
        assert client.post(
            f"{PREFIX}/agents/grammar_expert_001/enable"
        ).status_code == 200
        assert client.get(f"{PREFIX}/overview").json()["agents_disabled"] == 0


class TestOrchestratorRoute:
    def test_unknown_task_is_400(self, client: TestClient) -> None:
        r = client.post(f"{PREFIX}/run", json={"task": "nope"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "unknown_task"

    def test_validate_grammar(self, client: TestClient) -> None:
        r = client.post(
            f"{PREFIX}/run",
            json={"task": "validate_grammar", "source_text": "keitu paasian lo"},
        )
        assert r.status_code == 200, r.text
        assert "is_valid" in r.json()["result"]


class TestPublicReadSurface:
    """Learning reads and the orchestrator are public; tuning is not."""

    @pytest.mark.parametrize(
        "path",
        [
            "/overview",
            "/agents",
            "/agents/translator_001",
            "/skills",
            "/skills/continuous_learning/agents",
        ],
    )
    def test_reads_are_public(self, path: str) -> None:
        assert rbac.is_public_path("GET", f"{PREFIX}{path}")
        assert rbac.classify_route("GET", f"{PREFIX}{path}") == "public"

    def test_run_is_public(self) -> None:
        assert rbac.is_public_path("POST", f"{PREFIX}/run")
        assert rbac.classify_route("POST", f"{PREFIX}/run") == "public"

    def test_run_uses_the_strict_anonymous_bucket(self) -> None:
        """/run is CPU/SQLite-heavy, so it must not get the 120/min bucket."""
        assert f"{PREFIX}/run" in rbac.PUBLIC_CHAT_PATHS

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/agents"),
            ("PATCH", "/agents/translator_001"),
            ("DELETE", "/agents/translator_001"),
            ("POST", "/agents/translator_001/enable"),
            ("POST", "/agents/translator_001/disable"),
            ("POST", "/skills"),
        ],
    )
    def test_mutations_stay_admin(self, method: str, path: str) -> None:
        assert rbac.is_admin_path(f"{PREFIX}{path}", method)
        assert rbac.classify_route(method, f"{PREFIX}{path}") == "admin"
        assert not rbac.is_public_path(method, f"{PREFIX}{path}")

    def test_get_on_agents_prefix_stays_public_despite_admin_prefixes(self) -> None:
        """The read verb must not be captured by the admin write entries."""
        assert rbac.classify_route("GET", f"{PREFIX}/agents") == "public"
        assert rbac.classify_route("POST", f"{PREFIX}/agents") == "admin"


class TestAnonymousBehaviour:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/overview"),
            ("GET", "/agents"),
            ("GET", "/skills"),
            ("POST", "/run"),
        ],
    )
    def test_public_routes_answer_anonymous_under_enforce(
        self, monkeypatch: pytest.MonkeyPatch, method: str, path: str
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.request(
            method,
            f"{PREFIX}{path}",
            json={"task": "validate_grammar", "source_text": "keitu paasian lo"}
            if method == "POST"
            else None,
        )
        assert r.status_code == 200, r.text

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/agents"),
            ("PATCH", "/agents/grammar_expert_001"),
            ("DELETE", "/agents/grammar_expert_001"),
            ("POST", "/agents/grammar_expert_001/disable"),
            ("POST", "/agents/grammar_expert_001/enable"),
            ("POST", "/skills"),
        ],
    )
    def test_anonymous_cannot_tune_under_enforce(
        self, monkeypatch: pytest.MonkeyPatch, method: str, path: str
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.request(
            method,
            f"{PREFIX}{path}",
            json={"agent_id": "x", "name": "x", "role": "translator"},
        )
        assert r.status_code == 401, r.text

    def test_anonymous_limit_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.post(f"{PREFIX}/run", json={"task": "build_vocabulary", "limit": 5000})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["limit_applied"] <= 50
        assert body["limit_clamped"] is True

    def test_small_limit_is_not_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.post(f"{PREFIX}/run", json={"task": "build_vocabulary", "limit": 5})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["limit_applied"] == 5
        assert body["limit_clamped"] is False


class TestOrchestratorReuse:
    """``/run`` must not build a fresh orchestrator per request.

    Each instance starts its own agent tasks and queue processor and re-reads the
    whole Bible corpus — the cold load alone measured 12.9 s, so a task spent most
    of its timeout budget being constructed.
    """

    def test_same_database_reuses_one_orchestrator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zolai.api import learning_agents_router as router

        monkeypatch.setattr(router, "_orchestrator_cache", None)
        first = router._orchestrator()
        assert router._orchestrator() is first

    def test_other_database_builds_a_new_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from zolai.api import learning_agents_router as router
        from zolai.config import config

        monkeypatch.setattr(router, "_orchestrator_cache", None)
        first = router._orchestrator()
        monkeypatch.setattr(config.paths, "db", tmp_path / "other.db")
        assert router._orchestrator() is not first

    def test_task_history_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from zolai.api import learning_agents_router as router
        from zolai.learning.agents.learning_orchestrator import COMPLETED_TASK_HISTORY

        monkeypatch.setattr(router, "_orchestrator_cache", None)
        assert router._orchestrator().completed_tasks.maxlen == COMPLETED_TASK_HISTORY


class TestBatchedWordContexts:
    """``build_contexts_for_words`` replaces a per-word full corpus sweep.

    Vocabulary building asked for one sweep per word — 1000 words re-read 30k
    verses each, which is how ``build_vocabulary`` reached its timeout.
    """

    def _learner(self):
        from collections import Counter, defaultdict

        from zolai.learning.bible_pattern_learner import BiblePatternLearner

        learner = BiblePatternLearner.__new__(BiblePatternLearner)  # no corpus load
        learner.verses = [
            {
                "ref": "GEN 1:1",
                "zo_tdb77": "Tinte in hnuei hlauhna a thlen salh.",
                "en_kJV": "In the beginning God created.",
            },
            {
                "ref": "GEN 1:2",
                "zo_tdb77": "Thil sa in topa hi.",
                "en_kJV": "The earth was empty.",
            },
        ]
        learner.patterns = defaultdict(list)
        learner.word_freq = Counter()
        learner.phrase_patterns = Counter()
        return learner

    def test_answers_for_every_word_in_one_pass(self) -> None:
        learner = self._learner()
        contexts = learner.build_contexts_for_words(["tinte", "thil", "nei"], limit=2)

        assert set(contexts) == {"tinte", "thil", "nei"}
        assert "GEN 1:1" in contexts["tinte"]
        assert "GEN 1:2" in contexts["thil"]
        assert "No Bible sentences" in contexts["nei"]

    def test_matches_the_single_word_lookup(self) -> None:
        learner = self._learner()
        batch = learner.build_contexts_for_words(["tinte"], limit=2)
        assert batch["tinte"] == learner.build_context_for_word("tinte", limit=2)

    def test_refresh_replaces_instead_of_accumulating(self) -> None:
        """The background cycle must not double the counts every hour."""
        learner = self._learner()
        first_patterns, first_freq, _ = learner.fresh_patterns()
        second_patterns, second_freq, _ = learner.fresh_patterns()

        assert sum(len(v) for v in second_patterns.values()) == sum(
            len(v) for v in first_patterns.values()
        )
        assert second_freq == first_freq

        learner.extract_patterns()
        learner.extract_patterns()
        assert sum(len(v) for v in learner.patterns.values()) == 2 * sum(
            len(v) for v in first_patterns.values()
        )
