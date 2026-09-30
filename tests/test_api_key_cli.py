"""CLI tests for ``zolai apikey`` — issue / list / rotate / revoke (ADR-014).

Covers the operator half of backlog P0-1:

- ``create`` prints the plaintext **once** (``--json``) and stores only the hash
- ``list`` never reveals a secret
- ``rotate`` kills the old secret immediately and mints a replacement
- ``revoke`` takes effect on the very next verification
- error paths exit 1 with a readable message (bad scope, unknown id/target)
- issue/rotate/revoke append ``data_audit_log`` rows
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from zolai.api import auth
from zolai.cli.main import app as cli_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store wired into both the CLI and the auth service."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'cli.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _run(runner: CliRunner, *args: str):
    result = runner.invoke(cli_app, ["apikey", *args])
    result.clean = _ANSI.sub("", result.stdout or "")  # type: ignore[attr-defined]
    return result


def _json(result) -> dict:
    return json.loads(result.clean)


def _db_text(mgr: DatabaseManager, table: str) -> str:
    with mgr.engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table}")).fetchall()
    return " ".join(str(value) for row in rows for value in row._mapping.values() if value)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_json_shows_plaintext_once_and_stores_hash_only(
    runner: CliRunner, auth_db
) -> None:
    result = _run(
        runner,
        "create",
        "--name",
        "mcp-server",
        "--scopes",
        "dataset:read,catalog:read",
        "--json",
    )
    assert result.exit_code == 0, result.stdout

    payload = _json(result)
    plaintext = payload["plaintext"]
    assert plaintext.startswith("zolai_sk_")
    assert payload["key_hash"] == auth.hash_key(plaintext)
    assert payload["key_prefix"] == plaintext[: auth.PREFIX_LEN]
    assert payload["scopes"] == ["dataset:read", "catalog:read"]

    # Only the hash is at rest — the secret appears nowhere in the store.
    assert plaintext not in _db_text(auth_db, "api_keys")
    assert auth.hash_key(plaintext) in _db_text(auth_db, "api_keys")

    # Audit row for the issue.
    assert "create" in _db_text(auth_db, "data_audit_log")


def test_create_default_output_is_a_table_with_the_secret_once(
    runner: CliRunner, auth_db
) -> None:
    result = _run(runner, "create", "-n", "desktop", "--scopes", "dataset:read")
    assert result.exit_code == 0, result.stdout
    assert "API key created" in result.clean
    assert "Plaintext (shown once, not stored)" in result.clean


def test_create_rejects_unknown_scope_and_exits_1(runner: CliRunner, auth_db) -> None:
    result = _run(runner, "create", "-n", "bad", "--scopes", "not-an-action")
    assert result.exit_code == 1
    assert "unknown scope" in result.clean
    assert auth.list_api_keys() == []


def test_create_rejects_duplicate_name(runner: CliRunner, auth_db) -> None:
    first = _run(runner, "create", "-n", "dupe", "--scopes", "dataset:read", "--json")
    assert first.exit_code == 0
    second = _run(runner, "create", "-n", "dupe", "--scopes", "dataset:read", "--json")
    assert second.exit_code == 1
    assert "already exists" in second.clean


def test_create_with_expiry_records_it(runner: CliRunner, auth_db) -> None:
    result = _run(
        runner, "create", "-n", "temp", "--scopes", "dataset:read",
        "--expires-days", "30", "--json",
    )
    assert result.exit_code == 0, result.stdout
    assert _json(result)["expires_at"] is not None


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_json_has_no_plaintext(runner: CliRunner, auth_db) -> None:
    created = _run(runner, "create", "-n", "ci", "--scopes", "pipeline:run", "--json")
    plaintext = _json(created)["plaintext"]

    listed = _run(runner, "list", "--json")
    assert listed.exit_code == 0, listed.stdout
    assert plaintext not in listed.clean  # secret never shown again

    payload = _json(listed)
    assert payload["count"] == len(payload["items"]) == 1
    assert payload["items"][0]["name"] == "ci"
    assert payload["items"][0]["key_hash"] == auth.hash_key(plaintext)


def test_list_empty_table_hints_at_create(runner: CliRunner, auth_db) -> None:
    listed = _run(runner, "list")
    assert listed.exit_code == 0
    assert "No API keys yet" in listed.clean


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


def test_rotate_mints_replacement_and_kills_old_secret(
    runner: CliRunner, auth_db
) -> None:
    created = _json(_run(runner, "create", "-n", "svc", "--scopes", "dataset:read", "--json"))
    old_plaintext = created["plaintext"]
    assert auth.resolve_key(old_plaintext) is not None

    rotated = _run(runner, "rotate", str(created["id"]), "--json")
    assert rotated.exit_code == 0, rotated.stdout

    payload = _json(rotated)
    new_plaintext = payload["plaintext"]
    assert payload["old_id"] == created["id"]
    assert payload["old_revoked_at"]
    assert new_plaintext != old_plaintext
    # Scopes and name carry over to the replacement.
    assert payload["scopes"] == ["dataset:read"]
    assert payload["name"].startswith("svc")

    auth.invalidate_key_cache()
    assert auth.resolve_key(old_plaintext) is None  # old secret is dead
    assert auth.resolve_key(new_plaintext) is not None

    # Two rows, old one revoked; both secrets still absent at rest.
    items = auth.list_api_keys()
    assert len(items) == 2
    assert auth.get_api_key(created["id"])["revoked_at"] is not None
    stored = _db_text(auth_db, "api_keys")
    assert old_plaintext not in stored and new_plaintext not in stored

    # Issue + revoke audit rows.
    audit = _db_text(auth_db, "data_audit_log")
    assert "rotated" in audit


def test_rotate_unknown_id_exits_1(runner: CliRunner, auth_db) -> None:
    result = _run(runner, "rotate", "9999", "--json")
    assert result.exit_code == 1
    assert "not found" in result.clean


def test_rotate_already_revoked_exits_1(runner: CliRunner, auth_db) -> None:
    created = _json(_run(runner, "create", "-n", "x", "--scopes", "dataset:read", "--json"))
    assert _run(runner, "revoke", str(created["id"]), "--json").exit_code == 0
    result = _run(runner, "rotate", str(created["id"]), "--json")
    assert result.exit_code == 1
    assert "already revoked" in result.clean


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


def test_revoke_by_id_takes_effect_immediately(runner: CliRunner, auth_db) -> None:
    created = _json(_run(runner, "create", "-n", "bye", "--scopes", "dataset:read", "--json"))
    plaintext = created["plaintext"]
    assert auth.resolve_key(plaintext) is not None

    revoked = _run(runner, "revoke", str(created["id"]), "--json")
    assert revoked.exit_code == 0, revoked.stdout
    assert _json(revoked)["revoked_at"]

    auth.invalidate_key_cache()
    assert auth.resolve_key(plaintext) is None  # rejected from the next request

    audit = _db_text(auth_db, "data_audit_log")
    assert "apikey revoked" in audit
    assert plaintext not in _db_text(auth_db, "data_audit_log")  # no secret in audit


def test_revoke_by_prefix(runner: CliRunner, auth_db) -> None:
    created = _json(_run(runner, "create", "-n", "pref", "--scopes", "dataset:read", "--json"))
    prefix = created["key_prefix"]

    result = _run(runner, "revoke", prefix, "--json")
    assert result.exit_code == 0, result.stdout
    assert _json(result)["id"] == created["id"]


def test_revoke_unknown_target_exits_1(runner: CliRunner, auth_db) -> None:
    result = _run(runner, "revoke", "zolai_sk_nope", "--json")
    assert result.exit_code == 1
    assert "No API key matches" in result.clean


def test_revoke_twice_exits_1(runner: CliRunner, auth_db) -> None:
    created = _json(_run(runner, "create", "-n", "twice", "--scopes", "dataset:read", "--json"))
    assert _run(runner, "revoke", str(created["id"]), "--json").exit_code == 0
    second = _run(runner, "revoke", str(created["id"]), "--json")
    assert second.exit_code == 1
    assert "already revoked" in second.clean
