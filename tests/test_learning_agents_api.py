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


class TestAdminOnly:
    def test_prefix_is_classified_admin(self) -> None:
        assert rbac.classify_route("GET", f"{PREFIX}/overview") == "admin"
        assert rbac.classify_route("POST", f"{PREFIX}/run") == "admin"
        assert rbac.classify_route(
            "PATCH", f"{PREFIX}/agents/translator_001"
        ) == "admin"

    def test_routes_are_not_public(self) -> None:
        assert not rbac.is_public_path("GET", f"{PREFIX}/overview")
        assert not rbac.is_public_path("GET", f"{PREFIX}/agents")

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/overview"),
            ("GET", "/agents"),
            ("POST", "/agents/grammar_expert_001/disable"),
            ("POST", "/run"),
        ],
    )
    def test_anonymous_is_401_under_enforce(
        self, monkeypatch: pytest.MonkeyPatch, method: str, path: str
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        c = TestClient(app)
        r = c.request(method, f"{PREFIX}{path}",
                      json={"task": "translate"} if method == "POST" and path == "/run" else None)
        assert r.status_code == 401, r.text
        assert r.json()["detail"]["error"] == "unauthorized"
