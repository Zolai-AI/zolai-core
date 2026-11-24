"""TTS registry tests — migration, audit, RBAC, plain-scalar API, publish gate.

Covers the signed-off BUILD contract:

- ``create_tts_tables`` DDL: additive ``IF NOT EXISTS``, idempotent, wired into
  ``run_all_migrations`` (not ``apply_migrations``);
- writes go through ``DatabaseManager.bulk_upsert`` and land in
  ``data_audit_log``;
- ``GET /api/v1/tts/*``: anon **401** under enforce · member **200** · unknown
  ``voice_key`` **404** · unreachable DB **503** · plain JSON scalars only
  (no internal enums) · no write/publish route exists;
- ``legal_status != 'verified'`` reports ``publishable: false`` (gate blocked).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from zolai.api import auth
from zolai.api import tts as tts_api
from zolai.api.rbac import classify_route
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    TTS_GENERATIONS_DDL,
    TTS_VOICES_DDL,
    create_data_audit_log_table,
    create_tts_tables,
    run_all_migrations,
)

EXPECTED_VOICE_COLUMNS = {
    "id", "voice_key", "provider", "provider_voice_id", "style_default",
    "is_active", "legal_status", "legal_notes", "created_at", "updated_at",
}
EXPECTED_GENERATION_COLUMNS = {
    "id", "voice_id", "batch_id", "input_sha256", "source_ref", "model",
    "api_version", "style", "manifest_path", "created_at",
}


@pytest.fixture()
def tmp_db() -> Iterator[DatabaseManager]:
    """Fresh, initialized SQLite store (no TTS tables yet)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    yield mgr
    mgr.dispose()
    Path(db_path).unlink(missing_ok=True)


def _tables(mgr: DatabaseManager) -> set[str]:
    return set(inspect(mgr.engine).get_table_names())


def _columns(mgr: DatabaseManager, table: str) -> set[str]:
    return {c["name"] for c in inspect(mgr.engine).get_columns(table)}


def _seed_voice(mgr: DatabaseManager, *, key: str = "seed-voice-1",
                provider: str = "openrouter",
                legal_status: str = "unverified") -> None:
    """Write a voice row through ``bulk_upsert`` (the audited write path)."""
    mgr.bulk_upsert(
        "tts_voices",
        [{
            "voice_key": key,
            "provider": provider,
            "provider_voice_id": "heygen-default-1",
            "style_default": "narration",
            "is_active": 1,
            "legal_status": legal_status,
            "legal_notes": None,
        }],
        conflict_columns=["voice_key"],
        user="tts-test",
    )


# ---------------------------------------------------------------------------
# Migration: DDL shape, idempotence, wiring
# ---------------------------------------------------------------------------


class TestTtsMigration:
    def test_creates_both_tables_and_indexes(self, tmp_db: DatabaseManager) -> None:
        assert "tts_voices" not in _tables(tmp_db)

        result = create_tts_tables(tmp_db)

        assert result["errors"] == []
        assert set(result["created"]) == {"tts_voices", "tts_generations"}
        tables = _tables(tmp_db)
        assert {"tts_voices", "tts_generations"} <= tables
        index_names = set()
        for table in ("tts_voices", "tts_generations"):
            index_names |= {ix["name"] for ix in inspect(tmp_db.engine).get_indexes(table)}
        assert "ix_tts_generations_batch" in index_names
        assert "ix_tts_voices_provider" in index_names

    def test_schema_matches_contract(self, tmp_db: DatabaseManager) -> None:
        create_tts_tables(tmp_db)
        assert _columns(tmp_db, "tts_voices") == EXPECTED_VOICE_COLUMNS
        assert _columns(tmp_db, "tts_generations") == EXPECTED_GENERATION_COLUMNS
        # legal_status NOT NULL default 'unverified' — publish gate input.
        with tmp_db.engine.connect() as conn:
            row = conn.execute(text("PRAGMA table_info(tts_voices)")).fetchall()
        by_name = {r.name: r for r in row}
        assert by_name["legal_status"].notnull == 1
        assert by_name["voice_key"].notnull == 1
        # owner/license/consent_ref live in the tracked manifest ONLY.
        assert "owner" not in by_name
        assert "license" not in by_name
        assert "consent_ref" not in by_name

    def test_generation_references_voice(self, tmp_db: DatabaseManager) -> None:
        create_tts_tables(tmp_db)
        with tmp_db.engine.connect() as conn:
            fks = conn.execute(
                text("PRAGMA foreign_key_list(tts_generations)")
            ).fetchall()
        assert any(
            r._mapping["table"] == "tts_voices" and r._mapping["from"] == "voice_id"
            for r in fks
        )

    def test_second_run_skips_without_rebuilding(self, tmp_db: DatabaseManager) -> None:
        first = create_tts_tables(tmp_db)
        assert set(first["created"]) == {"tts_voices", "tts_generations"}
        _seed_voice(tmp_db)

        second = create_tts_tables(tmp_db)

        assert second["created"] == []
        assert second["errors"] == []
        assert "tts_voices (already exists)" in second["skipped"]
        assert "tts_generations (already exists)" in second["skipped"]
        with tmp_db.engine.connect() as conn:
            kept = conn.execute(text("SELECT voice_key FROM tts_voices")).fetchall()
        assert [r.voice_key for r in kept] == ["seed-voice-1"]  # rows survive

    def test_ddl_is_create_if_not_exists_additive(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS tts_voices" in TTS_VOICES_DDL
        assert "CREATE TABLE IF NOT EXISTS tts_generations" in TTS_GENERATIONS_DDL
        assert "DROP" not in TTS_VOICES_DDL.upper().replace("DROPPED", "")
        assert "DROP" not in TTS_GENERATIONS_DDL.upper().replace("DROPPED", "")

    def test_run_all_migrations_wiring(self, tmp_db: DatabaseManager) -> None:
        result = run_all_migrations(tmp_db)

        assert "tts_tables" in result
        assert result["tts_tables"]["errors"] == []
        assert {"tts_voices", "tts_generations"} <= _tables(tmp_db)

        # Whole-set idempotence: second full pass stays green.
        again = run_all_migrations(tmp_db)
        assert again["tts_tables"]["errors"] == []
        assert again["tts_tables"]["created"] == []


# ---------------------------------------------------------------------------
# Writes via bulk_upsert ⇒ data_audit_log
# ---------------------------------------------------------------------------


class TestBulkUpsertAudit:
    def test_insert_and_update_are_audit_logged(self, tmp_db: DatabaseManager) -> None:
        create_tts_tables(tmp_db)
        create_data_audit_log_table(tmp_db)

        first = tmp_db.bulk_upsert(
            "tts_voices",
            [{
                "voice_key": "seed-voice-1", "provider": "openrouter",
                "provider_voice_id": "heygen-default-1", "is_active": 1,
                "legal_status": "unverified",
            }],
            conflict_columns=["voice_key"],
            user="tts-test",
        )
        assert first == {"inserted": 1, "updated": 0, "errors": 0}

        second = tmp_db.bulk_upsert(
            "tts_voices",
            [{
                "voice_key": "seed-voice-1", "provider": "openrouter",
                "provider_voice_id": "heygen-default-1", "is_active": 1,
                "legal_status": "verified",
            }],
            conflict_columns=["voice_key"],
            user="tts-test",
        )
        assert second == {"inserted": 0, "updated": 1, "errors": 0}

        with tmp_db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT field, reason FROM data_audit_log "
                    "WHERE table_name = 'tts_voices' ORDER BY id"
                )
            ).fetchall()
        assert [r.field for r in rows] == ["bulk_upsert_insert", "bulk_upsert_update"]
        assert all(r.reason == "bulk_upsert:tts-test" for r in rows)

        # Only one row exists (upsert, not duplicate insert).
        with tmp_db.engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM tts_voices")).scalar()
        assert count == 1

    def test_generations_write_is_audit_logged(self, tmp_db: DatabaseManager) -> None:
        create_tts_tables(tmp_db)
        create_data_audit_log_table(tmp_db)
        _seed_voice(tmp_db)
        with tmp_db.engine.connect() as conn:
            voice_id = conn.execute(
                text("SELECT id FROM tts_voices WHERE voice_key = 'seed-voice-1'")
            ).scalar_one()

        result = tmp_db.bulk_upsert(
            "tts_generations",
            [{
                "voice_id": voice_id, "batch_id": "batch-001",
                "input_sha256": "abc123", "model": "heygen/heygen-voice-1-turbo:free",
                "manifest_path": "manifests/batch-001.json",
            }],
            conflict_columns=["batch_id", "input_sha256"],
            user="tts-test",
        )
        assert result == {"inserted": 1, "updated": 0, "errors": 0}
        with tmp_db.engine.connect() as conn:
            field = conn.execute(
                text(
                    "SELECT field FROM data_audit_log "
                    "WHERE table_name = 'tts_generations'"
                )
            ).scalar_one()
        assert field == "bulk_upsert_insert"


# ---------------------------------------------------------------------------
# API: RBAC, 404, 503, plain scalars, no write route, publish gate
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway key store wired into the auth service (singleton seam)."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'auth.db'}")
    mgr.init_db()
    from zolai.data.migrations import create_api_keys_table

    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def tts_client(tmp_db, auth_db, monkeypatch) -> TestClient:
    """App (no lifespan) with TTS tables + a seeded voice on throwaway stores."""
    create_tts_tables(tmp_db)
    _seed_voice(tmp_db)
    monkeypatch.setattr(tts_api, "_get_manager", lambda: tmp_db)
    return TestClient(create_app())


def _member_headers(auth_db: DatabaseManager) -> dict[str, str]:
    key = auth.create_api_key(
        name="tts-member", scopes=["dataset:read"], created_by="test"
    )
    return {"X-API-Key": key["plaintext"]}


class TestRbac:
    def test_anonymous_is_401_under_enforce(self, tts_client: TestClient,
                                            monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        for path in ("/api/v1/tts/voices", "/api/v1/tts/voices/seed-voice-1",
                     "/api/v1/tts/generations"):
            resp = tts_client.get(path)
            assert resp.status_code == 401, (path, resp.status_code, resp.text[:200])

    def test_member_is_200(self, tts_client: TestClient, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        headers = _member_headers(auth_db)
        resp = tts_client.get("/api/v1/tts/voices", headers=headers)
        assert resp.status_code == 200, resp.text[:300]
        body = resp.json()
        assert body["count"] == 1
        assert body["voices"][0]["voice_key"] == "seed-voice-1"

    def test_route_class_is_member_not_unclassified(self) -> None:
        for path in ("/api/v1/tts/voices", "/api/v1/tts/voices/x",
                     "/api/v1/tts/generations"):
            assert classify_route("GET", path) == "member", path


class TestVoiceEndpoint:
    def test_unknown_voice_key_is_404(self, tts_client: TestClient, auth_db,
                                      monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = tts_client.get(
            "/api/v1/tts/voices/does-not-exist", headers=_member_headers(auth_db)
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "not_found"
        assert detail["voice_key"] == "does-not-exist"

    def test_db_unreachable_is_503(self, tts_client: TestClient, auth_db,
                                   monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")

        def boom() -> None:
            raise RuntimeError("database is gone")

        monkeypatch.setattr(tts_api, "_get_manager", boom)
        resp = tts_client.get(
            "/api/v1/tts/voices", headers=_member_headers(auth_db)
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["reason"] == "database_unreachable"

    def test_voice_detail_publishable_false_for_unverified(
        self, tts_client: TestClient, auth_db, monkeypatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        resp = tts_client.get(
            "/api/v1/tts/voices/seed-voice-1", headers=_member_headers(auth_db)
        )
        assert resp.status_code == 200
        voice = resp.json()["voice"]
        assert voice["legal_status"] == "unverified"
        assert voice["publishable"] is False  # free-tier terms unverified ⇒ blocked

    def test_verified_voice_reports_publishable(
        self, tts_client: TestClient, auth_db, monkeypatch, tmp_db
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        _seed_voice(tmp_db, key="verified-voice", legal_status="verified")
        resp = tts_client.get(
            "/api/v1/tts/voices/verified-voice", headers=_member_headers(auth_db)
        )
        assert resp.status_code == 200
        assert resp.json()["voice"]["publishable"] is True


class TestPlainScalarsAndReadOnly:
    def test_no_internal_enums_in_payload(self, tts_client: TestClient, auth_db,
                                          monkeypatch) -> None:
        """Every value is a JSON primitive — no enum objects, no reprs."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        headers = _member_headers(auth_db)

        def check(value: object, depth: int = 0) -> None:
            assert depth < 10, "payload too deep"
            if isinstance(value, dict):
                for key, val in value.items():
                    assert isinstance(key, str)
                    check(val, depth + 1)
            elif isinstance(value, list):
                for val in value:
                    check(val, depth + 1)
            else:
                assert value is None or isinstance(value, (str, int, float, bool)), (
                    f"non-scalar in payload: {type(value)!r}"
                )

        for path in ("/api/v1/tts/voices", "/api/v1/tts/voices/seed-voice-1",
                     "/api/v1/tts/generations"):
            resp = tts_client.get(path, headers=headers)
            assert resp.status_code == 200, (path, resp.text[:200])
            check(resp.json())

    def test_no_write_routes_exposed(self, tts_client: TestClient, auth_db,
                                     monkeypatch) -> None:
        """Read-only surface: no POST/PUT/PATCH/DELETE, no publish route."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        headers = _member_headers(auth_db)
        spec = tts_client.app.openapi()
        tts_paths = {p: ops for p, ops in spec["paths"].items()
                     if p.startswith("/api/v1/tts")}
        assert tts_paths, "expected /api/v1/tts routes"
        for path, ops in tts_paths.items():
            methods = {m.upper() for m in ops}
            assert methods <= {"GET"}, (path, methods)
        # And a probe of the obvious publish verb answers method-not-allowed/404.
        for method in ("post", "put", "delete"):
            resp = getattr(tts_client, method)(
                "/api/v1/tts/voices/seed-voice-1/publish", headers=headers
            )
            assert resp.status_code in (404, 405), (method, resp.status_code)

    def test_generations_endpoint_reads_log(self, tts_client: TestClient, auth_db,
                                            monkeypatch, tmp_db) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        _seed_voice(tmp_db)
        with tmp_db.engine.connect() as conn:
            voice_id = conn.execute(
                text("SELECT id FROM tts_voices WHERE voice_key='seed-voice-1'")
            ).scalar_one()
        tmp_db.bulk_upsert(
            "tts_generations",
            [{"voice_id": voice_id, "batch_id": "b-9", "input_sha256": "d",
              "model": "heygen/heygen-voice-1-turbo:free"}],
            conflict_columns=["batch_id", "input_sha256"],
            user="tts-test",
        )
        resp = tts_client.get(
            "/api/v1/tts/generations", params={"batch_id": "b-9"},
            headers=_member_headers(auth_db),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["generations"][0]["batch_id"] == "b-9"
        assert body["generations"][0]["model"] == "heygen/heygen-voice-1-turbo:free"
