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
