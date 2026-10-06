"""CLI tests for ``zolai user`` — the account bootstrap path (no self-registration).

Covers the operator half of username accounts:

- ``create`` prompts (or reads ``--password-stdin``) and **never** accepts a
  default password; a duplicate username exits 1
- ``--json`` output carries no ``password_hash`` on any command
- ``list`` shows roles/enabled/last-login and no secret
- ``disable`` / ``enable`` flip the flag and revoke (never resurrect) sessions
- ``password`` re-hashes and revokes every live session
- ``revoke-sessions`` reports a count, not a token
- every command appends ``data_audit_log`` rows with no secret in them
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from zolai.api import session_auth
from zolai.cli.main import app as cli_app
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_user_session_tables

PASSWORD = "correct horse battery staple"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def user_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Throwaway store wired into the session service and the CLI."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'user-cli.db'}")
    mgr.init_db()
    with mgr.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS sessions"))
        conn.execute(text("DROP TABLE IF EXISTS users"))
    create_user_session_tables(mgr)
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    session_auth.reset_session_cache()
    yield
    session_auth.reset_session_cache()


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _run(runner: CliRunner, *args: str, stdin: str | None = None):
    result = runner.invoke(cli_app, ["user", *args], input=stdin)
    result.clean = _ANSI.sub("", result.stdout or "")  # type: ignore[attr-defined]
    return result


def _json(result) -> dict:
    """Parse the JSON block of the output.

    A hidden prompt writes ``Password:`` to stdout before the payload, so the
    JSON starts at the first brace rather than at byte 0.
    """
    out = result.clean
    start = out.index("{")
    end = out.rindex("}") + 1
    return json.loads(out[start:end])


def _audit_blob(mgr: DatabaseManager) -> str:
    with mgr.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, row_id, field, old_value, new_value, reason "
                "FROM data_audit_log ORDER BY id"
            )
        ).fetchall()
    return " | ".join(str(value) for row in rows for value in row)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_from_stdin(runner: CliRunner, user_db) -> None:
    result = _run(
        runner, "create", "--username", "founder", "--role", "admin", "--json",
        "--password-stdin", stdin=f"{PASSWORD}\n",
    )

    assert result.exit_code == 0, result.stdout
    body = _json(result)
    assert body["username"] == "founder"
    assert body["role"] == "admin"
    assert body["enabled"] == 1
    assert "password_hash" not in body


def test_create_prompts_when_stdin_flag_is_absent(runner: CliRunner, user_db) -> None:
    """No ``--password`` option and no default: it always prompts."""
    result = _run(runner, "create", "--username", "ada", "--json", stdin=f"{PASSWORD}\n{PASSWORD}\n")

    assert result.exit_code == 0, result.stdout
    assert _json(result)["username"] == "ada"


def test_create_help_never_offers_a_password_argument(runner: CliRunner) -> None:
    result = runner.invoke(cli_app, ["user", "create", "--help"])

    assert result.exit_code == 0
    assert "--password-stdin" in result.stdout
    assert "--password " not in result.stdout.replace("--password-stdin", "")


def test_create_refuses_a_duplicate_username(runner: CliRunner, user_db) -> None:
    first = _run(
        runner, "create", "--username", "founder", "--json", "--password-stdin", stdin=f"{PASSWORD}\n"
    )
    assert first.exit_code == 0

    duplicate = _run(
        runner, "create", "--username", "FOUNDER", "--password-stdin", stdin=f"{PASSWORD}\n"
    )

    assert duplicate.exit_code == 1
    assert "already exists" in duplicate.clean


def test_create_rejects_an_invalid_username_and_a_short_password(
    runner: CliRunner, user_db
) -> None:
    bad_name = _run(
        runner, "create", "--username", "has space", "--password-stdin", stdin=f"{PASSWORD}\n"
    )
    assert bad_name.exit_code == 1
    assert "username must be" in bad_name.clean

    short = _run(runner, "create", "--username", "ada", "--password-stdin", stdin="short\n")
    assert short.exit_code == 1
    assert "at least" in short.clean


def test_create_rejects_an_unknown_role(runner: CliRunner, user_db) -> None:
    result = _run(
        runner, "create", "--username", "ada", "--role", "wizard",
        "--password-stdin", stdin=f"{PASSWORD}\n",
    )

    assert result.exit_code == 1
    assert "unknown role" in result.clean


def test_create_without_stdin_fails_loudly(runner: CliRunner, user_db) -> None:
    result = _run(runner, "create", "--username", "ada", "--password-stdin", stdin="")

    assert result.exit_code == 1
    assert "No password on stdin" in result.clean


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_shows_users_and_never_a_hash(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--role", "admin",
         "--password-stdin", stdin=f"{PASSWORD}\n")

    result = _run(runner, "list", "--json")

    assert result.exit_code == 0
    body = _json(result)
    assert body["count"] == 1
    assert body["items"][0]["username"] == "founder"
    assert "password_hash" not in json.dumps(body)
    assert "$argon2" not in result.clean

    table = _run(runner, "list")
    assert table.exit_code == 0
    assert "founder" in table.clean
    assert "$argon2" not in table.clean


def test_list_is_empty_without_users(runner: CliRunner, user_db) -> None:
    result = _run(runner, "list")

    assert result.exit_code == 0
    assert "No users yet" in result.clean


# ---------------------------------------------------------------------------
# disable / enable
# ---------------------------------------------------------------------------


def test_disable_and_enable_round_trip(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--password-stdin", stdin=f"{PASSWORD}\n")

    disabled = _run(runner, "disable", "founder", "--json")
    assert disabled.exit_code == 0
    assert _json(disabled)["enabled"] == 0

    with pytest.raises(session_auth.LoginRejected):
        session_auth.login("founder", PASSWORD)

    enabled = _run(runner, "enable", "founder", "--json")
    assert _json(enabled)["enabled"] == 1
    assert session_auth.login("founder", PASSWORD)["token"].startswith("zolai_ss_")


def test_enable_does_not_resurrect_a_revoked_session(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--password-stdin", stdin=f"{PASSWORD}\n")
    token = session_auth.login("founder", PASSWORD)["token"]

    _run(runner, "disable", "founder")
    _run(runner, "enable", "founder")

    assert session_auth.resolve_session(token) is None


def test_disable_and_enable_report_an_unknown_user(runner: CliRunner, user_db) -> None:
    for command in ("disable", "enable"):
        result = _run(runner, command, "ghost")
        assert result.exit_code == 1
        assert "not found" in result.clean


# ---------------------------------------------------------------------------
# password
# ---------------------------------------------------------------------------


def test_password_change_rehashes_and_revokes_sessions(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--password-stdin", stdin=f"{PASSWORD}\n")
    token = session_auth.login("founder", PASSWORD)["token"]
    before = session_auth.get_user("founder")["password_hash"]

    new_password = "a brand new passphrase"
    # The prompt asks twice (hidden + confirmation), so stdin carries it twice.
    result = _run(
        runner, "password", "founder", "--json", stdin=f"{new_password}\n{new_password}\n"
    )

    assert result.exit_code == 0
    assert "password_hash" not in _json(result)
    after = session_auth.get_user("founder")["password_hash"]
    assert after != before
    assert after.startswith("$argon2id$")
    assert session_auth.resolve_session(token) is None
    assert session_auth.login("founder", "a brand new passphrase")["token"]


def test_password_rejects_a_short_new_password(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--password-stdin", stdin=f"{PASSWORD}\n")

    result = _run(runner, "password", "founder", stdin="tiny\ntiny\n")

    assert result.exit_code == 1
    assert "at least" in result.clean


def test_password_reports_an_unknown_user(runner: CliRunner, user_db) -> None:
    result = _run(runner, "password", "ghost", stdin=f"{PASSWORD}\n{PASSWORD}\n")

    assert result.exit_code == 1
    assert "not found" in result.clean


# ---------------------------------------------------------------------------
# revoke-sessions
# ---------------------------------------------------------------------------


def test_revoke_sessions_reports_a_count_not_a_token(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--password-stdin", stdin=f"{PASSWORD}\n")
    first = session_auth.login("founder", PASSWORD)["token"]
    second = session_auth.login("founder", PASSWORD)["token"]

    result = _run(runner, "revoke-sessions", "founder", "--json")

    assert result.exit_code == 0
    body = _json(result)
    assert body == {"username": "founder", "revoked": 2}
    assert first not in result.clean
    assert second not in result.clean
    assert session_auth.resolve_session(first) is None
    assert session_auth.resolve_session(second) is None


def test_revoke_sessions_reports_an_unknown_user(runner: CliRunner, user_db) -> None:
    result = _run(runner, "revoke-sessions", "ghost")

    assert result.exit_code == 1
    assert "not found" in result.clean


# ---------------------------------------------------------------------------
# Audit — every command writes a row, none with a secret
# ---------------------------------------------------------------------------


def test_every_command_audits_and_no_secret_is_recorded(runner: CliRunner, user_db) -> None:
    _run(runner, "create", "--username", "founder", "--role", "admin",
         "--password-stdin", stdin=f"{PASSWORD}\n")
    token = session_auth.login("founder", PASSWORD)["token"]
    _run(runner, "disable", "founder")
    _run(runner, "enable", "founder")
    _run(runner, "password", "founder", stdin="a brand new passphrase\na brand new passphrase\n")
    _run(runner, "revoke-sessions", "founder")

    blob = _audit_blob(user_db)

    for field in ("create", "login", "disabled", "enabled", "password", "revoke_sessions"):
        assert f"| {field} |" in blob or blob.endswith(f"| {field} |"), field
    assert PASSWORD not in blob
    assert "a brand new passphrase" not in blob
    assert "$argon2" not in blob
    assert token not in blob
    assert session_auth.hash_token(token) not in blob
    assert "zolai_ss_" not in blob
