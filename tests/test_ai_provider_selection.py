"""Phase B — provider/model selection, refresh-models, public catalog.

Covers the plan's done-when:

- optional per-request ``provider``/``model`` override on public chat, admin
  chat and agent runs: validated **before** the run (DB read only — a real
  404/400 even in ``rule`` mode, zero sockets) and echoed back as
  ``requested_provider`` / ``requested_model`` (``""`` when no override;
  ``provider``/``model`` keep meaning *actually used*);
- ``seed_catalog`` activates ``pcore-brain`` when no row is active and never
  clobbers an admin choice (asserted in ``test_ai_providers_api``);
- ``POST /admin/ai-providers/{id}/refresh-models``: a remote fetch persists
  (``source="remote"``); any failure — and every brain/custom/gemini row —
  degrades to the catalog list (``source="catalog"``, never 500);
- ``GET /api/v1/providers``: public in every auth mode, enabled rows only,
  exactly five fields, zero secrets;
- ``adapter.resolve_model`` falls back to ``models[0]`` for a blank
  ``selected_model`` (plan item 7).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_agent_runs import _SEED_SQL
from test_engine_contract import network_blocked

from zolai.api import ai_providers_router, auth, rbac
from zolai.api.agent_router import _reset_run_bucket
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.providers_router import validate_selection
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    create_ai_provider_tables,
    create_api_keys_table,
    create_knowledge_tables,
)
from zolai.llm import adapter, catalog
from zolai.llm import provider_settings as settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()


@pytest.fixture()
def provider_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway DB with api_keys + ai_providers, seeded, wired into auth + settings."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'selection_providers.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    create_ai_provider_tables(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(settings, "_get_manager", lambda: mgr)
    seed = catalog.seed_catalog(mgr)
    assert seed["errors"] == []
    yield mgr
    mgr.dispose()


@pytest.fixture()
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Seeded retrieval/store DB + rule mode (offline by construction)."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    create_knowledge_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
    yield db_path
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def admin_key(provider_db: DatabaseManager) -> dict:
    """Admin role (settings:write) + agent scope — one key, all admin surfaces."""
    return auth.create_api_key(
        name="selection-admin",
        scopes=["settings:read", "settings:write", "agent:run", "agent:read"],
        created_by="test",
    )


@pytest.fixture()
def runner_key(provider_db: DatabaseManager) -> dict:
    return auth.create_api_key(
        name="selection-runner", scopes=["agent:run", "agent:read"], created_by="test"
    )


@pytest.fixture()
def member_key(provider_db: DatabaseManager) -> dict:
    return auth.create_api_key(name="selection-member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


def _brain_model(provider_db) -> str:
    row = settings.get_provider_row("pcore-brain", provider_db)
    return json.loads(row["models"])[0]


# ---------------------------------------------------------------------------
# validate_selection — unit (stable codes, no HTTP)
# ---------------------------------------------------------------------------


class TestValidateSelectionUnit:
    def test_no_override_is_a_noop(self, provider_db) -> None:
        assert validate_selection(None, None) is None

    def test_unknown_provider_is_404_provider_unknown(self, provider_db) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_selection("ghost-provider")
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "provider_unknown"
        assert exc.value.detail["code"] == "PROVIDER_UNKNOWN"

    def test_disabled_provider_is_404_provider_inactive(self, provider_db) -> None:
        settings.upsert_provider("openai", fields={"enabled": 0})
        with pytest.raises(HTTPException) as exc:
            validate_selection("openai")
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "provider_inactive"

    def test_model_not_on_row_is_400_provider_model_unknown(self, provider_db) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_selection("pcore-brain", "totally-made-up")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "provider_model_unknown"
        assert exc.value.detail["code"] == "PROVIDER_MODEL_UNKNOWN"

    def test_adapter_error_is_400_not_500(self, provider_db) -> None:
        """A bare ``adapter.ProviderError`` must map too (dual-exception fix)."""
        settings.upsert_provider("ollama", fields={"models": [], "selected_model": ""})
        with pytest.raises(HTTPException) as exc:
            validate_selection("ollama")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "model_not_configured"
        assert exc.value.detail["code"] == adapter.MODEL_NOT_CONFIGURED

    def test_valid_selection_resolves_row_and_first_model(self, provider_db) -> None:
        resolved = validate_selection("pcore-brain", None)
        assert resolved is not None
        assert resolved["catalog_id"] == "pcore-brain"
        assert resolved["model"] == _brain_model(provider_db)


class TestResolveModelFallback:
    def test_blank_selected_model_falls_back_to_models_first(self, provider_db) -> None:
        # Plan item 7: a blank selected_model picks the row's first listed
        # model — never a guess, never a 500.
        row = dict(settings.get_provider_row("pcore-brain", provider_db))
        row["selected_model"] = ""
        assert adapter.resolve_model(row) == json.loads(row["models"])[0]

    def test_resolve_selection_without_model_resolves_first(self, provider_db) -> None:
        resolved = settings.resolve_selection("pcore-brain", None)
        assert resolved["model"] == _brain_model(provider_db)


# ---------------------------------------------------------------------------
# Public chat — override echo, 4xx contract, zero sockets in rule mode
# ---------------------------------------------------------------------------


class TestPublicChatOverride:
    def test_override_echo_and_zero_sockets(
        self, client, provider_db, data_db
    ) -> None:
        model = _brain_model(provider_db)
        with network_blocked() as attempts:
            resp = client.post(
                "/api/v1/assistant/chat",
                json={"message": "pasian", "provider": "pcore-brain", "model": model},
            )
        assert attempts == [], f"validation/chat attempted network: {attempts}"
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requested_provider"] == "pcore-brain"
        assert body["requested_model"] == model
        # rule mode → honest retrieval fallback; provider/model = actually used
        assert body["retrieval_only"] is True
        assert body["provider"] == "" and body["model"] == ""
        assert isinstance(body["citations"], list) and body["citations"]

    def test_no_override_echoes_empty_strings(self, client, provider_db, data_db) -> None:
        resp = client.post("/api/v1/assistant/chat", json={"message": "pasian"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requested_provider"] == ""
        assert body["requested_model"] == ""

    def test_unknown_provider_is_404(self, client, provider_db) -> None:
        resp = client.post(
            "/api/v1/assistant/chat", json={"message": "hi", "provider": "ghost-provider"}
        )
        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "provider_unknown"
        assert detail["code"] == "PROVIDER_UNKNOWN"

    def test_disabled_provider_is_404(self, client, provider_db) -> None:
        settings.upsert_provider("openai", fields={"enabled": 0})
        resp = client.post(
            "/api/v1/assistant/chat", json={"message": "hi", "provider": "openai"}
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["error"] == "provider_inactive"

    def test_unknown_model_is_400(self, client, provider_db) -> None:
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "hi", "provider": "pcore-brain", "model": "nope"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "provider_model_unknown"

    def test_adapter_error_is_400_in_http_path(self, client, provider_db) -> None:
        settings.upsert_provider("ollama", fields={"models": [], "selected_model": ""})
        resp = client.post(
            "/api/v1/assistant/chat", json={"message": "hi", "provider": "ollama"}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "model_not_configured"

    def test_public_route_stays_open_under_enforce_with_override(
        self, client, provider_db, data_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        model = _brain_model(provider_db)
        resp = client.post(
            "/api/v1/assistant/chat",
            json={"message": "pasian", "provider": "pcore-brain", "model": model},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["requested_provider"] == "pcore-brain"


class TestAdminChatOverride:
    def test_admin_chat_echoes_override(self, client, provider_db, data_db, admin_key) -> None:
        model = _brain_model(provider_db)
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "pasian", "provider": "pcore-brain", "model": model},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["requested_provider"] == "pcore-brain"
        assert body["requested_model"] == model
        assert body["retrieval_only"] is True  # rule mode

    def test_admin_chat_unknown_provider_is_404(
        self, client, provider_db, data_db, admin_key
    ) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "hi", "provider": "ghost-provider"},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["error"] == "provider_unknown"


# ---------------------------------------------------------------------------
# Agent runs — override echo + validation before the run starts
# ---------------------------------------------------------------------------


class TestAgentRunOverride:
    def test_run_echoes_override_offline(
        self, client, provider_db, data_db, runner_key
    ) -> None:
        model = _brain_model(provider_db)
        with network_blocked() as attempts:
            resp = client.post(
                "/api/v1/agent/runs",
                json={
                    "goal": "summarize what pasian means",
                    "provider": "pcore-brain",
                    "model": model,
                },
                headers=_headers(runner_key),
            )
        assert attempts == [], f"rule-mode run attempted network: {attempts}"
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["requested_provider"] == "pcore-brain"
        assert run["requested_model"] == model
        assert run["status"] == "succeeded"
        assert run["mode"] == "rule"
        assert run["provider"] == "" and run["model"] == ""  # actually used

    def test_unknown_provider_404_and_no_run_created(
        self, client, provider_db, data_db, runner_key
    ) -> None:
        resp = client.post(
            "/api/v1/agent/runs",
            json={"goal": "x", "provider": "ghost-provider"},
            headers=_headers(runner_key),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["error"] == "provider_unknown"
        listing = client.get("/api/v1/agent/runs", headers=_headers(runner_key))
        assert listing.status_code == 200
        assert listing.json()["count"] == 0  # validation failed before create_run

    def test_unknown_model_is_400(self, client, provider_db, data_db, runner_key) -> None:
        resp = client.post(
            "/api/v1/agent/runs",
            json={"goal": "x", "provider": "pcore-brain", "model": "nope"},
            headers=_headers(runner_key),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["error"] == "provider_model_unknown"


# ---------------------------------------------------------------------------
# refresh-models — remote persists, failure degrades, brain/gemini never fetch
# ---------------------------------------------------------------------------


class TestRefreshModels:
    def test_remote_success_persists(self, client, provider_db, admin_key, monkeypatch) -> None:
        def _fake_fetch(row, timeout_s: float = 15.0) -> list[str]:
            return ["m1", "m2"]

        monkeypatch.setattr(ai_providers_router, "_fetch_remote_models", _fake_fetch)
        resp = client.post(
            "/api/v1/admin/ai-providers/openai/refresh-models", headers=_headers(admin_key)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["catalog_id"] == "openai"
        assert body["models"] == ["m1", "m2"]
        assert body["source"] == "remote"
        row = settings.get_provider_row("openai", provider_db)
        assert json.loads(row["models"]) == ["m1", "m2"]  # persisted

    def test_fetch_failure_degrades_to_catalog_never_500(
        self, client, provider_db, admin_key, monkeypatch
    ) -> None:
        settings.upsert_provider("openai", fields={"models": ["keep-me"]})

        def _boom(row, timeout_s: float = 15.0) -> list[str]:
            raise RuntimeError("no network")

        monkeypatch.setattr(ai_providers_router, "_fetch_remote_models", _boom)
        resp = client.post(
            "/api/v1/admin/ai-providers/openai/refresh-models", headers=_headers(admin_key)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "catalog"
        assert body["models"] == list(catalog.find_catalog("openai").models)
        row = settings.get_provider_row("openai", provider_db)
        assert json.loads(row["models"]) == ["keep-me"]  # never persisted

    @pytest.mark.parametrize("cid", ["pcore-brain", "google-gemini"])
    def test_brain_and_gemini_never_fetch(
        self, client, provider_db, admin_key, monkeypatch, cid
    ) -> None:
        calls: list[str] = []

        def _recorder(row, timeout_s: float = 15.0) -> list[str]:
            calls.append(str(row.get("catalog_id")))
            return ["x"]

        monkeypatch.setattr(ai_providers_router, "_fetch_remote_models", _recorder)
        resp = client.post(
            f"/api/v1/admin/ai-providers/{cid}/refresh-models", headers=_headers(admin_key)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "catalog"
        assert body["models"] == list(catalog.find_catalog(cid).models)
        assert calls == []  # never attempted (brain/custom have no listing; gemini shape differs)

    def test_unknown_id_is_404(self, client, admin_key) -> None:
        resp = client.post(
            "/api/v1/admin/ai-providers/ghost/refresh-models", headers=_headers(admin_key)
        )
        assert resp.status_code == 404

    def test_anon_is_401(self, client, provider_db) -> None:
        assert client.post("/api/v1/admin/ai-providers/openai/refresh-models").status_code == 401

    def test_member_is_403(self, client, provider_db, member_key) -> None:
        resp = client.post(
            "/api/v1/admin/ai-providers/openai/refresh-models", headers=_headers(member_key)
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "forbidden"


class TestModelsEndpointUnit:
    def test_strips_chat_completions(self) -> None:
        from zolai.api.ai_providers_router import _models_endpoint

        assert (
            _models_endpoint("https://api.openai.com/v1/chat/completions")
            == "https://api.openai.com/v1/models"
        )

    def test_strips_completions(self) -> None:
        from zolai.api.ai_providers_router import _models_endpoint

        assert _models_endpoint("https://x.example/v1/completions") == "https://x.example/v1/models"

    def test_appends_to_bare_base(self) -> None:
        from zolai.api.ai_providers_router import _models_endpoint

        assert _models_endpoint("https://x.example/v1") == "https://x.example/v1/models"
        assert _models_endpoint("https://x.example/v1/") == "https://x.example/v1/models"

    def test_empty_base_is_models(self) -> None:
        from zolai.api.ai_providers_router import _models_endpoint

        assert _models_endpoint("") == "/models"


# ---------------------------------------------------------------------------
# Public catalog — anon-safe, five fields, zero secrets, enabled rows only
# ---------------------------------------------------------------------------


class TestPublicCatalog:
    def test_route_is_declared_public(self) -> None:
        assert ("GET", "/api/v1/providers") in rbac.PUBLIC_ROUTES

    def test_anonymous_catalog_five_fields_no_secrets(self, client, provider_db) -> None:
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["count"] == 7
        for item in payload["items"]:
            assert set(item) == {"catalog_id", "name", "adapter", "models", "selected_model"}
        assert "api_key_ref" not in resp.text
        assert "enc:v1" not in resp.text
        assert '"secret"' not in resp.text

    def test_disabled_rows_are_excluded(self, client, provider_db) -> None:
        settings.upsert_provider("ollama", fields={"enabled": 0})
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["count"] == 6
        ids = [i["catalog_id"] for i in payload["items"]]
        assert "ollama" not in ids

    def test_open_in_enforce_mode(self, client, provider_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        assert client.get("/api/v1/providers").status_code == 200
