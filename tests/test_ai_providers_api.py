"""P1 — AI provider catalog: seeding, masking, dispatch, admin API.

Covers the plan's done-when:

- catalog seeded on boot (7 rows incl. ``pcore-brain``), idempotent,
  ``catalog_id`` stable / display name renameable;
- brain URL from ``AI_BRAIN_URL``/``PCORE_BRAIN_URL`` + env-key order;
  **unit test proves the brain request body has no ``tools`` key**, native
  ``tools`` only for ``openai``/``openrouter``;
- ``GET /admin/ai-providers`` masked (no plaintext anywhere); ``PUT``/
  ``activate``/``test`` strict (401 anon, 403 member-without-scope); unknown id
  → real 404; ``pick_provider`` honors enable/disable and returns
  ``NO_ACTIVE_PROVIDER`` / ``MODEL_NOT_CONFIGURED`` instead of guessing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables, create_api_keys_table
from zolai.llm import adapter, catalog
from zolai.llm import provider_settings as settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway DB with api_keys + ai_providers tables, seeded, wired in."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'providers.db'}")
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
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def admin_key(provider_db: DatabaseManager) -> dict:
    """Admin key — requests ``provider_db`` so ``auth`` is wired to the throwaway DB."""
    return auth.create_api_key(
        name="settings-admin", scopes=["settings:read", "settings:write"], created_by="test"
    )


@pytest.fixture()
def member_key(provider_db: DatabaseManager) -> dict:
    return auth.create_api_key(name="member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


# ---------------------------------------------------------------------------
# Catalog seeding
# ---------------------------------------------------------------------------


class TestCatalogSeed:
    def test_seven_rows_including_brain(self, provider_db) -> None:
        rows = settings.list_provider_rows(provider_db)
        ids = [r["catalog_id"] for r in rows]
        assert len(ids) == 7
        assert ids == sorted(ids)
        assert catalog.BRAIN_CATALOG_ID in ids

    def test_seed_is_idempotent(self, provider_db) -> None:
        again = catalog.seed_catalog(provider_db)
        assert again["created"] == []
        assert len(again["skipped"]) == 7
        assert len(settings.list_provider_rows(provider_db)) == 7

    def test_seed_never_overwrites_admin_state(self, provider_db) -> None:
        settings.upsert_provider("openai", fields={"name": "Our OpenAI", "enabled": 0})
        catalog.seed_catalog(provider_db)
        row = settings.get_provider_row("openai", provider_db)
        assert row is not None
        assert row["name"] == "Our OpenAI"
        assert row["enabled"] == 0

    def test_catalog_id_is_the_join_key(self, provider_db) -> None:
        settings.upsert_provider("pcore-brain", fields={"name": "Renamed Brain"})
        # Rename never changes the stable identity.
        assert settings.get_provider_row("pcore-brain", provider_db) is not None
        assert settings.get_provider_row("Renamed Brain", provider_db) is None

    def test_brain_row_models_and_env_keys(self) -> None:
        entry = catalog.find_catalog(catalog.BRAIN_CATALOG_ID)
        assert entry is not None
        assert entry.adapter == "brain"
        assert entry.env_keys == (
            "AI_BRAIN_API_KEY",
            "PCORE_BRIDGE_API_KEY",
            "AI_API_KEY",
        )
        assert list(entry.models) == [
            "opencode/nemotron-3-ultra-free",
            "opencode/mimo-v2.6-flash-free",
            "opencode/muse-spark-1.3-contributor-free",
            "opencode/big-pickle",
        ]


# ---------------------------------------------------------------------------
# Brain URL / key resolution
# ---------------------------------------------------------------------------


class TestBrainResolution:
    def test_default_brain_url(self, monkeypatch) -> None:
        monkeypatch.delenv("AI_BRAIN_URL", raising=False)
        monkeypatch.delenv("PCORE_BRAIN_URL", raising=False)
        assert catalog.brain_base_url() == "https://pcore-brain.peterlianpi.site/v1"
        assert catalog.brain_completions_url() == (
            "https://pcore-brain.peterlianpi.site/v1/chat/completions"
        )

    def test_env_override_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("PCORE_BRAIN_URL", "https://brain.internal/v1/")
        monkeypatch.delenv("AI_BRAIN_URL", raising=False)
        assert catalog.brain_completions_url() == "https://brain.internal/v1/chat/completions"

    def test_ai_brain_url_preferred_over_pcore(self, monkeypatch) -> None:
        monkeypatch.setenv("AI_BRAIN_URL", "https://a.example/v1")
        monkeypatch.setenv("PCORE_BRAIN_URL", "https://b.example/v1")
        assert catalog.brain_base_url() == "https://a.example/v1"

    def test_brain_key_env_order(self, monkeypatch) -> None:
        monkeypatch.delenv("AI_BRAIN_API_KEY", raising=False)
        monkeypatch.delenv("PCORE_BRIDGE_API_KEY", raising=False)
        monkeypatch.delenv("AI_API_KEY", raising=False)
        entry = catalog.find_catalog(catalog.BRAIN_CATALOG_ID)
        assert entry is not None
        assert catalog.read_catalog_env_key(entry) == ""

        monkeypatch.setenv("AI_API_KEY", "third")
        assert catalog.read_catalog_env_key(entry) == "third"
        monkeypatch.setenv("PCORE_BRIDGE_API_KEY", "second")
        assert catalog.read_catalog_env_key(entry) == "second"
        monkeypatch.setenv("AI_BRAIN_API_KEY", "first")
        assert catalog.read_catalog_env_key(entry) == "first"

    def test_brain_completions_url_is_idempotent(self, monkeypatch) -> None:
        monkeypatch.setenv("AI_BRAIN_URL", "https://x.example/v1/chat/completions")
        assert catalog.brain_completions_url() == "https://x.example/v1/chat/completions"


# ---------------------------------------------------------------------------
# Adapter dispatch — the brain must never see a native `tools` key
# ---------------------------------------------------------------------------


class TestAdapterDispatch:
    def test_brain_body_has_no_tools_key(self) -> None:
        """Structural guarantee: the brain path never receives ``tools``."""
        row = {"adapter": "brain", "catalog_id": "pcore-brain"}
        body = adapter.build_chat_body(
            row,
            "opencode/mimo-v2.6-flash-free",
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "rag_search", "parameters": {}}}],
        )
        assert "tools" not in body
        assert set(body) <= {"model", "messages", "temperature", "max_tokens"}

    @pytest.mark.parametrize("native", ["openai", "openrouter"])
    def test_native_tools_only_for_openai_wire_adapters(self, native: str) -> None:
        row = {"adapter": native, "catalog_id": native}
        tools = [{"type": "function", "function": {"name": "rag_search"}}]
        body = adapter.build_chat_body(row, "m", [{"role": "user", "content": "hi"}], tools=tools)
        assert body["tools"] == tools

    @pytest.mark.parametrize("adapter_name", ["brain", "custom"])
    def test_non_native_adapters_drop_tools(self, adapter_name: str) -> None:
        row = {"adapter": adapter_name, "catalog_id": "x"}
        tools = [{"type": "function", "function": {"name": "rag_search"}}]
        body = adapter.build_chat_body(row, "m", [{"role": "user", "content": "hi"}], tools=tools)
        assert "tools" not in body

    def test_supports_native_tools_is_openai_only(self) -> None:
        assert adapter.supports_native_tools("openai") is True
        assert adapter.supports_native_tools("openrouter") is True
        assert adapter.supports_native_tools("brain") is False
        assert adapter.supports_native_tools("custom") is False

    def test_to_wire_model_strips_openrouter_prefix(self) -> None:
        assert adapter.to_wire_model("openrouter/nvidia/nemotron:free") == "nvidia/nemotron:free"
        assert adapter.to_wire_model("gpt-4o-mini") == "gpt-4o-mini"

    def test_native_tool_types_from_catalog(self) -> None:
        """Catalog adapter values decide native tools — never the display name."""
        native = {"openai", "openrouter"}
        for cid in ("openai", "openrouter", "pcore-brain", "anthropic", "google-gemini", "groq", "ollama"):
            entry = catalog.find_catalog(cid)
            assert entry is not None
            assert adapter.supports_native_tools(entry.adapter) is (entry.adapter in native)


# ---------------------------------------------------------------------------
# pick_provider / resolve_model — stable errors, never a guess
# ---------------------------------------------------------------------------


class TestPickProvider:
    def test_no_active_provider_when_everything_disabled(self, provider_db) -> None:
        for row in settings.list_provider_rows(provider_db):
            settings.upsert_provider(row["catalog_id"], fields={"enabled": 0})
        with pytest.raises(adapter.ProviderError) as exc:
            adapter.pick_provider(provider_db)
        assert exc.value.code == adapter.NO_ACTIVE_PROVIDER

    def test_active_row_wins_over_first_row(self, provider_db) -> None:
        settings.activate_provider("openrouter", provider_db)
        row = adapter.pick_provider(provider_db)
        assert row["catalog_id"] == "openrouter"

    def test_seed_activates_brain_when_no_row_active(self, provider_db) -> None:
        # Phase B §8: a fresh seed activates pcore-brain so a new install has a
        # default; an admin's explicit choice elsewhere is never clobbered.
        rows = settings.list_provider_rows(provider_db)
        active = [r["catalog_id"] for r in rows if r["is_active"]]
        assert active == [catalog.BRAIN_CATALOG_ID]
        assert adapter.pick_provider(provider_db)["catalog_id"] == catalog.BRAIN_CATALOG_ID

    def test_seed_does_not_clobber_admin_active_choice(self, provider_db) -> None:
        settings.activate_provider("openrouter", provider_db)
        catalog.seed_catalog(provider_db)
        rows = settings.list_provider_rows(provider_db)
        assert [r["catalog_id"] for r in rows if r["is_active"]] == ["openrouter"]

    def test_falls_back_to_first_enabled_row(self, provider_db) -> None:
        # No active row (admin cleared them all) → first *enabled* row.
        from sqlalchemy import text

        with provider_db.engine.begin() as conn:
            conn.execute(text("UPDATE ai_providers SET is_active = 0"))
        rows = settings.list_provider_rows(provider_db)
        assert all(not r["is_active"] for r in rows)
        assert adapter.pick_provider(provider_db)["catalog_id"] == rows[0]["catalog_id"]

    def test_disabled_active_row_is_skipped(self, provider_db) -> None:
        settings.activate_provider("openrouter", provider_db)
        settings.upsert_provider("openrouter", fields={"enabled": 0})
        row = adapter.pick_provider(provider_db)
        assert row["catalog_id"] != "openrouter"
        assert row["enabled"] == 1

    def test_model_never_guessed(self, provider_db) -> None:
        row = dict(settings.get_provider_row("ollama", provider_db))
        row["selected_model"] = ""
        row["models"] = "[]"
        with pytest.raises(adapter.ProviderError) as exc:
            adapter.resolve_model(row)
        assert exc.value.code == adapter.MODEL_NOT_CONFIGURED

    def test_unknown_wanted_model_is_refused(self, provider_db) -> None:
        row = dict(settings.get_provider_row("pcore-brain", provider_db))
        with pytest.raises(adapter.ProviderError) as exc:
            adapter.resolve_model(row, "not-a-real-model")
        assert exc.value.code == adapter.MODEL_NOT_CONFIGURED


# ---------------------------------------------------------------------------
# Secrets — masked on read, env:/enc: at rest
# ---------------------------------------------------------------------------


class TestSecretMasking:
    def test_env_ref_never_returns_a_value(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
        masked = settings.mask_secret_ref("env:OPENAI_API_KEY")
        assert masked["mode"] == "env"
        assert masked["configured"] is True
        assert "sk-super-secret-value" not in json.dumps(masked)

    def test_enc_ref_is_redacted(self) -> None:
        masked = settings.mask_secret_ref("enc:v1:QUJDREVGR0g=")
        assert masked["mode"] == "encrypted"
        assert "QUJDREVGR0g=" not in masked["ref_masked"]

    def test_missing_ref_is_none(self) -> None:
        assert settings.mask_secret_ref(None) == {
            "mode": "none",
            "ref_masked": "",
            "configured": False,
        }

    def test_read_secret_ref_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MY_KEY", "plain-value")
        assert settings.read_secret_ref({"api_key_ref": "env:MY_KEY"}) == "plain-value"
        assert settings.read_secret_ref({"api_key_ref": None}) == ""

    def test_paste_without_encryption_is_refused(self) -> None:
        """No ``cryptography`` extra ⇒ plaintext PUT is refused (env: only)."""
        from importlib.util import find_spec

        if find_spec("cryptography") is not None:
            pytest.skip("cryptography installed — paste path available")
        with pytest.raises(settings.ProviderError) as exc:
            settings.encode_secret_ref("sk-whatever")
        assert exc.value.code == "SECRET_ENCRYPTION_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


class TestAdminAPI:
    def test_get_is_masked_and_never_leaks_a_key(
        self, client, provider_db, admin_key, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-never-in-a-response")
        settings.upsert_provider("openai", fields={}, secret_env="OPENAI_API_KEY")
        response = client.get("/api/v1/admin/ai-providers", headers=_headers(admin_key))
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 7
        assert "sk-never-in-a-response" not in response.text
        openai_row = next(i for i in payload["items"] if i["catalog_id"] == "openai")
        assert openai_row["secret"] == {
            "mode": "env",
            "ref_masked": "env:OPENAI_API_KEY",
            "configured": True,
        }
        brain = next(i for i in payload["items"] if i["catalog_id"] == "pcore-brain")
        assert brain["adapter"] == "brain"
        assert brain["base_url"] == ""
        assert brain["secret"]["mode"] == "none"

    def test_get_unknown_path_is_real_404(self, client, admin_key) -> None:
        response = client.get(
            "/api/v1/admin/ai-providers/nope", headers=_headers(admin_key)
        )
        assert response.status_code in {404, 405}

    def test_put_unknown_catalog_id_is_404(self, client, admin_key) -> None:
        response = client.put(
            "/api/v1/admin/ai-providers/does-not-exist",
            json={"name": "x"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "provider_not_found"

    def test_put_rename_keeps_catalog_id(self, client, provider_db, admin_key) -> None:
        response = client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"name": "Team OpenAI", "selected_model": "gpt-4o"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["catalog_id"] == "openai"
        assert body["name"] == "Team OpenAI"
        assert body["selected_model"] == "gpt-4o"

    def test_put_secret_env_is_masked_back(
        self, client, provider_db, admin_key, monkeypatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
        response = client.put(
            "/api/v1/admin/ai-providers/groq",
            json={"secret_env": "GROQ_API_KEY"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 200
        assert response.json()["secret"]["mode"] == "env"
        assert "gsk_secret" not in response.text

    def test_put_plaintext_secret_without_encryption_is_400(
        self, client, provider_db, admin_key
    ) -> None:
        from importlib.util import find_spec

        if find_spec("cryptography") is not None:
            pytest.skip("cryptography installed — paste path available")
        response = client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"secret": "sk-pasted"},
            headers=_headers(admin_key),
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "SECRET_ENCRYPTION_UNAVAILABLE"

    def test_put_without_secret_keeps_previous_ref(
        self, client, provider_db, admin_key, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "v1")
        settings.upsert_provider("openai", fields={}, secret_env="OPENAI_API_KEY")
        client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"name": "OpenAI Renamed"},
            headers=_headers(admin_key),
        )
        row = settings.get_provider_row("openai", provider_db)
        assert row["api_key_ref"] == "env:OPENAI_API_KEY"

    def test_clear_secret(self, client, provider_db, admin_key) -> None:
        settings.upsert_provider("openai", fields={}, secret_env="OPENAI_API_KEY")
        client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"clear_secret": True},
            headers=_headers(admin_key),
        )
        row = settings.get_provider_row("openai", provider_db)
        assert row["api_key_ref"] is None

    def test_activate_sets_exactly_one(
        self, client, provider_db, admin_key
    ) -> None:
        response = client.post(
            "/api/v1/admin/ai-providers/openrouter/activate", headers=_headers(admin_key)
        )
        assert response.status_code == 200
        assert response.json()["active_count"] == 1
        rows = settings.list_provider_rows(provider_db)
        active = [r["catalog_id"] for r in rows if r["is_active"]]
        assert active == ["openrouter"]

        client.post(
            "/api/v1/admin/ai-providers/ollama/activate", headers=_headers(admin_key)
        )
        rows = settings.list_provider_rows(provider_db)
        assert [r["catalog_id"] for r in rows if r["is_active"]] == ["ollama"]

    def test_activate_unknown_is_404(self, client, admin_key) -> None:
        response = client.post(
            "/api/v1/admin/ai-providers/ghost/activate", headers=_headers(admin_key)
        )
        assert response.status_code == 404

    def test_test_connection_shape(self, client, provider_db, admin_key, monkeypatch) -> None:
        monkeypatch.setattr(
            settings,
            "test_connection",
            lambda catalog_id, **kw: {
                "ok": True,
                "status": 200,
                "latency_ms": 42.5,
                "error": None,
            },
        )
        response = client.post(
            "/api/v1/admin/ai-providers/pcore-brain/test", headers=_headers(admin_key)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["latency_ms"] == 42.5
        assert body["error"] is None

    def test_delete_seeded_row_is_409(self, client, admin_key) -> None:
        response = client.delete(
            "/api/v1/admin/ai-providers/openai", headers=_headers(admin_key)
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "DELETE_FORBIDDEN"

    def test_delete_custom_row(self, client, provider_db, admin_key) -> None:
        from sqlalchemy import text

        with provider_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ai_providers (catalog_id, name, adapter, base_url, models, "
                    "selected_model, docs, requires_key, api_key_ref, enabled, is_active, tier, "
                    "timeout_s, metadata, created_at, updated_at) "
                    "VALUES ('custom-lm', 'Custom LM', 'custom', 'http://localhost:9999/v1/chat/completions', "
                    "'[\"m\"]', 'm', '', 0, NULL, 1, 0, 'standard', 60, '{}', "
                    "datetime('now'), datetime('now'))"
                )
            )
        response = client.delete(
            "/api/v1/admin/ai-providers/custom-lm", headers=_headers(admin_key)
        )
        assert response.status_code == 200
        assert settings.get_provider_row("custom-lm", provider_db) is None


class TestStrictScopes:
    def test_anon_is_401_in_warn(self, client, provider_db) -> None:
        # default warn mode + strict scope ⇒ absent key is still 401
        assert client.get("/api/v1/admin/ai-providers").status_code == 401

    def test_anon_is_401_in_enforce(self, client, provider_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        assert client.get("/api/v1/admin/ai-providers").status_code == 401

    def test_member_key_is_403(self, client, provider_db, member_key) -> None:
        response = client.get(
            "/api/v1/admin/ai-providers", headers=_headers(member_key)
        )
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "forbidden"

    def test_read_scope_cannot_write(
        self, client, provider_db, admin_key, member_key
    ) -> None:
        # settings:read grants GET but not PUT
        response = client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"name": "nope"},
            headers=_headers(member_key),
        )
        assert response.status_code == 403
        ok = client.put(
            "/api/v1/admin/ai-providers/openai",
            json={"name": "yes"},
            headers=_headers(admin_key),
        )
        assert ok.status_code == 200

    def test_off_mode_bypasses(self, client, provider_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "off")
        assert client.get("/api/v1/admin/ai-providers").status_code == 200


# ---------------------------------------------------------------------------
# Assistant pin resolution (used by P4)
# ---------------------------------------------------------------------------


class TestAssistantResolution:
    def test_no_pin_uses_global_active_row(self, provider_db) -> None:
        settings.activate_provider("pcore-brain", provider_db)
        resolved = settings.resolve_assistant_ai("public", provider_db)
        assert resolved["catalog_id"] == "pcore-brain"
        row = settings.get_provider_row("pcore-brain", provider_db)
        models = json.loads(row["models"])
        assert resolved["model"] in models  # selected or first-listed, never guessed

    def test_pin_to_unknown_provider_is_stable_code(self, provider_db) -> None:
        with provider_db.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(
                text(
                    "INSERT INTO assistant_ai_pins (assistant, catalog_id, model) "
                    "VALUES ('public', 'ghost-provider', '')"
                )
            )
        with pytest.raises(settings.ProviderError) as exc:
            settings.resolve_assistant_ai("public", provider_db)
        assert exc.value.code == settings.ASSISTANT_PROVIDER_NOT_FOUND

    def test_pin_model_must_exist_on_provider(self, provider_db) -> None:
        with provider_db.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(
                text(
                    "INSERT INTO assistant_ai_pins (assistant, catalog_id, model) "
                    "VALUES ('admin', 'pcore-brain', 'totally-made-up')"
                )
            )
        with pytest.raises(settings.ProviderError) as exc:
            settings.resolve_assistant_ai("admin", provider_db)
        assert exc.value.code == settings.ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER

    def test_pin_resolves_pinned_model(self, provider_db) -> None:
        with provider_db.engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(
                text(
                    "INSERT INTO assistant_ai_pins (assistant, catalog_id, model) "
                    "VALUES ('admin', 'pcore-brain', 'opencode/big-pickle')"
                )
            )
        resolved = settings.resolve_assistant_ai("admin", provider_db)
        assert resolved["model"] == "opencode/big-pickle"
        assert resolved["catalog_id"] == "pcore-brain"
