"""Username + password accounts and revocable sessions — service layer.

This is the identity counterpart to :mod:`zolai.api.auth` (machine API keys) and
is deliberately built so that **API-key auth is not weakened**:

- Passwords are stored as an **argon2id PHC hash** (:func:`hash_password`) —
  a plaintext password never reaches the database, a log line, an audit row, or
  an error message.
- Session tokens are minted as ``zolai_ss_<token_urlsafe(32)>`` and stored as
  their **SHA-256 hash** (:func:`hash_token`), mirroring how :mod:`zolai.api.auth`
  handles keys. The plaintext is returned **once** by :func:`login` and is not
  reproducible afterwards.
- :func:`login` answers **one** failure shape — :class:`LoginRejected` with
  ``reason`` for the audit trail only — so unknown user, wrong password and a
  disabled account are indistinguishable to the caller. To keep the timing
  indistinguishable too, an unknown username still pays a **dummy argon2
  verify** against a fixed hash.
- Every mutation (create user, login, logout, enable/disable, password change,
  revoke-sessions) appends a ``data_audit_log`` row with **no secret in it**.
- Scope sets are strict subsets of the frozen 33-action vocabulary in
  :mod:`zolai.api.auth` — there is no ``*`` wildcard in either set.
- argon2 is CPU-bound (~200ms per verify here), so :func:`verify_login` is meant
  to be called from a thread pool by the router, and login is rate limited
  twice (per IP and per username) before any hash work happens.

Kill switch: ``ZOLAI_AUTH_SESSIONS=off`` (:func:`sessions_enabled`) makes the
router 404 and the middleware ignore ``zolai_ss_*`` tokens entirely.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text

logger = logging.getLogger("zolai.api.session_auth")

#: Plaintext session-token format.  The prefix doubles as the "this is a
#: session, not an API key" discriminator in the middleware.
SESSION_PREFIX = "zolai_ss_"

#: argon2id parameters (OWASP-aligned).  Expressed in the PHC string itself, so
#: a future parameter change re-hashes transparently on the next login.
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # 64 MiB
ARGON2_PARALLELISM = 4
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

#: A real argon2id hash at the parameters above, verified against when the
#: username does not exist (built lazily by :func:`_dummy_password_hash`).
#: Without it an unknown user would return in microseconds while a known one
#: takes ~200ms, which enumerates accounts.
_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
)

_dummy_lock = threading.Lock()
_dummy_hash: str | None = None


def _dummy_password_hash() -> str:
    """A real argon2id hash at our parameters, built lazily on first use.

    It is the digest of a value nobody knows; it is not a credential.  Built on
    first use rather than at import so ``import zolai.api.session_auth`` stays
    cheap (one ~200ms hash) for the CLI and the test suite.
    """
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = _hasher.hash("zolai-session-auth-timing-equalizer")
        return _dummy_hash

#: Session TTL (hours) when ``ZOLAI_SESSION_TTL_HOURS`` is unset or invalid.
DEFAULT_SESSION_TTL_HOURS = 12

#: Login attempts/minute per IP and per username when
#: ``ZOLAI_LOGIN_RATE_LIMIT_RPM`` is unset or invalid.
DEFAULT_LOGIN_RATE_LIMIT_RPM = 5

#: Per-username bucket is the looser of the two: an IP bucket of 5/min plus a
#: username bucket of 10/min means a shared-NAT office cannot lock a colleague
#: out with 5 tries, while a single-IP password spray is stopped at 5.
DEFAULT_LOGIN_RATE_LIMIT_USER_RPM = 10

#: Roles a ``users.role`` value may hold.  Unknown values degrade to
#: ``member`` (never raise — a hand-edited row must not 500 the API).
VALID_ROLES: frozenset[str] = frozenset({"member", "admin"})

#: Scope sets — strict subsets of the frozen 33-action vocabulary in
#: :mod:`zolai.api.auth` (asserted by tests).  Deliberately no ``*``.
MEMBER_SCOPES: tuple[str, ...] = (
    "dataset:read",
    "rag:read",
    "catalog:read",
    "agent:read",
    "agent:run",
)
ADMIN_EXTRA_SCOPES: tuple[str, ...] = (
    "apikey:manage",
    "settings:read",
    "settings:write",
    "user:manage",
    "role:manage",
)
ADMIN_SCOPES: tuple[str, ...] = MEMBER_SCOPES + ADMIN_EXTRA_SCOPES

#: Usernames are 1–64 chars of ``[a-z0-9._-]`` (lowercased) — filesystem-safe,
#: URL-safe, and unambiguous in logs.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

#: Reasons recorded on the audit row for a rejected login.  The HTTP response
#: carries none of these (single shape) — they exist for operators only.
REASON_UNKNOWN_USER = "unknown_user"
REASON_BAD_PASSWORD = "bad_password"
REASON_DISABLED = "disabled"

#: Minimum spacing between ``last_used_at`` writes per session (seconds).
_LAST_USED_THROTTLE_S = 60.0

#: Positive/negative verification cache TTL (seconds) and its hard size cap.
#: Both mirror :mod:`zolai.api.auth`: the TTL is logical only, so the cap is
#: what stops a scan presenting unique garbage tokens from growing the dict.
_CACHE_TTL_S = 10.0
_CACHE_PRUNE_SIZE = 4096

_cache_lock = threading.Lock()
#: token_hash -> (monotonic expiry, record | None)
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}

_used_lock = threading.Lock()
#: session id -> monotonic time of the last last_used_at write
_LAST_USED: dict[int, float] = {}


def _stamp(moment: datetime | None = None) -> str:
    """UTC ``YYYY-MM-DD HH:MM:SS`` stamp — the format every table here uses."""
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Config resolvers (env wins at call time so ops can flip without a restart)
# ---------------------------------------------------------------------------


def sessions_enabled() -> bool:
    """Whether username/session auth is on (``ZOLAI_AUTH_SESSIONS``, default on)."""
    raw = os.environ.get("ZOLAI_AUTH_SESSIONS")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "off", "false", "no", "disabled"}


def session_ttl_hours() -> int:
    """Session lifetime in hours (``ZOLAI_SESSION_TTL_HOURS``, default 12)."""
    raw = os.environ.get("ZOLAI_SESSION_TTL_HOURS")
    if raw is None or not raw.strip():
        return DEFAULT_SESSION_TTL_HOURS
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_TTL_HOURS


def login_rate_limit_rpm() -> int:
    """Login attempts/minute per IP (``ZOLAI_LOGIN_RATE_LIMIT_RPM``, default 5)."""
    raw = os.environ.get("ZOLAI_LOGIN_RATE_LIMIT_RPM")
    if raw is None or not raw.strip():
        return DEFAULT_LOGIN_RATE_LIMIT_RPM
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_LOGIN_RATE_LIMIT_RPM


def login_rate_limit_user_rpm() -> int:
    """Login attempts/minute per username (default 10).

    Independent of the per-IP bucket on purpose — that is what makes the pair
    useful. The defaults (IP 5/min, username 10/min) stop a single-host spray at
    5, while letting five people behind one NAT office sign in to *different*
    accounts. A tighter username setting than the IP setting is honoured too,
    which is the correct shape for one account under attack from many hosts.
    """
    raw = os.environ.get("ZOLAI_LOGIN_RATE_LIMIT_USER_RPM")
    if raw is None or not raw.strip():
        return DEFAULT_LOGIN_RATE_LIMIT_USER_RPM
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_LOGIN_RATE_LIMIT_USER_RPM


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def normalize_username(username: str) -> str:
    """Lowercase + validate a username.

    Raises:
        ValueError: on anything outside ``[a-z0-9][a-z0-9._-]{0,63}``.
    """
    candidate = str(username or "").strip().lower()
    if not USERNAME_RE.match(candidate):
        raise ValueError(
            "username must be 1-64 characters of a-z, 0-9, dot, underscore or hyphen "
            "and start with a letter or digit"
        )
    return candidate


def validate_password(password: str) -> str:
    """Validate a plaintext password length.

    Only the length is policed — no complexity rule, because length is what
    actually resists guessing, and a rule that rejects passphrases pushes
    people toward ``Passw0rd!``.

    Raises:
        ValueError: outside ``[MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH]``.
    """
    candidate = str(password or "")
    if len(candidate) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(candidate) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    return candidate


# ---------------------------------------------------------------------------
# Password + token primitives
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return an argon2id PHC string for ``password`` (the only stored form)."""
    return _hasher.hash(validate_password(password))


def verify_password_hash(password_hash: str, password: str) -> bool:
    """Verify ``password`` against a stored PHC string. Never raises.

    A malformed stored hash returns ``False`` rather than raising, so a
    corrupted row cannot turn into a 500 — and the caller still pays the same
    argon2 cost via the dummy hash, so timing does not split.
    """
    try:
        _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensive: a foreign hasher/format
        logger.exception("argon2 verify failed on a stored hash")
        return False
    return True


def _burn_argon2_cycles(password: str) -> None:
    """Spend the argon2 cost of a real verify without a matching user."""
    verify_password_hash(_dummy_password_hash(), password)


def generate_session_token() -> str:
    """Mint a plaintext session token (returned once, never stored)."""
    return SESSION_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a session token — the only form at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_session_token(token: str | None) -> bool:
    """Whether ``token`` looks like a session token (not an API key)."""
    return bool(token) and str(token).startswith(SESSION_PREFIX)


# ---------------------------------------------------------------------------
# Manager accessor (monkeypatch seam for tests)
# ---------------------------------------------------------------------------


def _get_manager() -> Any:
    """Resolve the DatabaseManager singleton (late import avoids cycles)."""
    from ..data.database import get_manager

    return get_manager()


def ensure_user_session_tables() -> dict[str, Any]:
    """Idempotently create ``users`` + ``sessions`` (used by the CLI first)."""
    from ..data.migrations import create_user_session_tables

    return create_user_session_tables(_get_manager())


# ---------------------------------------------------------------------------
# Roles + scopes
# ---------------------------------------------------------------------------


def role_scopes(role: str | None) -> list[str]:
    """Scope set for a role. Unknown/empty role degrades to ``member``.

    Never raises: a hand-edited ``users.role`` must not be able to 500 the API.
    """
    normalized = str(role or "").strip().lower()
    if normalized == "admin":
        return list(ADMIN_SCOPES)
    return list(MEMBER_SCOPES)


def record_from_user(user: dict[str, Any]) -> dict[str, Any]:
    """Build the auth record published on ``state["api_key"]`` for a session.

    Shape-compatible with an ``api_keys`` record so ``role_for`` /
    ``require_scope`` / ``require_role`` work unchanged, with two honest
    differences (D7): ``key_prefix`` is ``None`` (a session has no key prefix)
    and identity travels in ``username`` / ``display_name`` / ``user_id``.
    """
    role = str(user.get("role") or "member").strip().lower()
    # ``id`` for a ``users`` row, ``user_id`` for the sessions JOIN row.
    user_id = user.get("id") if user.get("id") is not None else user.get("user_id")
    username = user.get("username")
    return {
        "id": user_id,
        "name": username,
        "key_prefix": None,
        "scopes": role_scopes(role),
        "auth_source": "session",
        "username": username,
        "display_name": user.get("display_name") or username,
        "user_id": user_id,
        "role": role,
        "session_id": user.get("session_id"),
        "expires_at": user.get("session_expires_at"),
    }


# ---------------------------------------------------------------------------
# Verification cache + throttled bookkeeping
# ---------------------------------------------------------------------------


def invalidate_session_cache(token_hash: str | None = None) -> None:
    """Drop cached session verifications (all, or one) after a mutation."""
    with _cache_lock:
        if token_hash is None:
            _CACHE.clear()
        else:
            _CACHE.pop(token_hash, None)


def reset_session_cache() -> None:
    """Clear the verification cache and the last-used throttles (tests)."""
    invalidate_session_cache()
    with _used_lock:
        _LAST_USED.clear()


def _prune_cache(now: float) -> None:
    """Drop expired entries, then enforce the hard size cap (oldest first).

    Caller must hold ``_cache_lock``.  Mirrors ``zolai.api.auth._prune_cache``:
    a flood of unique garbage tokens must not grow the dict without limit.
    """
    expired = [key for key, (expires_at, _record) in _CACHE.items() if now >= expires_at]
    for key in expired:
        _CACHE.pop(key, None)
    while len(_CACHE) >= _CACHE_PRUNE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))


def _touch_last_used(session_id: Any) -> None:
    """Throttled ``last_used_at`` update (≤ 1 write/60s per session)."""
    if session_id is None:
        return
    now = time.monotonic()
    with _used_lock:
        last = _LAST_USED.get(int(session_id))
        if last is not None and now - last < _LAST_USED_THROTTLE_S:
            return
        _LAST_USED[int(session_id)] = now
    try:
        with _get_manager().engine.begin() as conn:
            conn.execute(
                text("UPDATE sessions SET last_used_at = :stamp WHERE id = :id"),
                {"stamp": _stamp(), "id": int(session_id)},
            )
    except Exception:
        logger.debug("sessions.last_used_at update skipped for session %s", session_id)


# ---------------------------------------------------------------------------
# Audit — data_audit_log rows, never a secret
# ---------------------------------------------------------------------------


def record_audit(
    *,
    table: str,
    row_id: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
    reason: str,
) -> None:
    """Append a ``users`` / ``sessions`` event to ``data_audit_log``.

    Callers pass only non-secret values (a username, a role, a session id, a
    count).  Nothing derived from a password hash or a token ever reaches an
    audit row.
    """
    try:
        with _get_manager().engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO data_audit_log "
                    "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
                    "VALUES (:table, :row_id, :field, :old_value, :new_value, :changed_at, :reason)"
                ),
                {
                    "table": table,
                    "row_id": int(row_id),
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "changed_at": _stamp(),
                    "reason": reason,
                },
            )
    except Exception:
        # Never break the operation on an audit hiccup — but make it loud.
        logger.exception("data_audit_log write failed for %s row %s", table, row_id)


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


_USER_COLUMNS = (
    "id, username, password_hash, display_name, role, enabled, created_at, updated_at, last_login"
)


def _row_to_user(row: Any) -> dict[str, Any]:
    user = dict(row._mapping)
    user["enabled"] = int(user.get("enabled") or 0)
    return user


def create_user(
    *,
    username: str,
    password: str,
    display_name: str | None = None,
    role: str = "member",
    actor: str = "cli",
) -> dict[str, Any]:
    """Create an account. There is **no** default password and no sign-up path.

    Returns the record (with ``password_hash`` included — the CLI must never
    print it; :func:`sanitize_user` exists for that).

    Raises:
        ValueError: invalid username/password/role, or a duplicate username.
    """
    name = normalize_username(username)
    digest = hash_password(password)
    role_value = str(role or "member").strip().lower()
    if role_value not in VALID_ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(VALID_ROLES)}")

    mgr = _get_manager()
    try:
        with mgr.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, password_hash, display_name, role) "
                    "VALUES (:username, :password_hash, :display_name, :role)"
                ),
                {
                    "username": name,
                    "password_hash": digest,
                    "display_name": display_name or None,
                    "role": role_value,
                },
            )
            row = conn.execute(
                text(f"SELECT {_USER_COLUMNS} FROM users WHERE username = :username"),
                {"username": name},
            ).first()
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise ValueError(f"username '{name}' already exists") from exc
        raise
    if row is None:  # pragma: no cover - defensive
        raise ValueError("failed to persist the new user")

    user = _row_to_user(row)
    invalidate_session_cache()
    record_audit(
        table="users",
        row_id=int(user["id"]),
        field="create",
        old_value=None,
        new_value=name,
        reason=f"user created by {actor} with role {role_value}",
    )
    return user


def get_user(username: str) -> dict[str, Any] | None:
    """Fetch one user by username (case-insensitive) or ``None``."""
    name = normalize_username(username)
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {_USER_COLUMNS} FROM users WHERE username = :username"),
            {"username": name},
        ).first()
    return _row_to_user(row) if row is not None else None


def list_users() -> list[dict[str, Any]]:
    """All user records ordered by id (hashes included — see :func:`sanitize_user`)."""
    with _get_manager().engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {_USER_COLUMNS} FROM users ORDER BY id")).fetchall()
    return [_row_to_user(row) for row in rows]


def sanitize_user(user: dict[str, Any]) -> dict[str, Any]:
    """Strip ``password_hash`` — the shape that is safe to log, return or print."""
    return {key: value for key, value in user.items() if key != "password_hash"}


def sanitize_users(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """:func:`sanitize_user` over a list."""
    return [sanitize_user(user) for user in users]


def set_user_enabled(username: str, *, enabled: bool, actor: str = "cli") -> dict[str, Any]:
    """Enable/disable an account.

    Disabling revokes that user's live sessions in the same transaction, so a
    disabled account cannot keep using a token it already holds.

    Raises:
        LookupError: no such user.
    """
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    flag = 1 if enabled else 0
    revoked_at = None if enabled else _stamp()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET enabled = :enabled, updated_at = :stamp WHERE id = :id"),
            {"enabled": flag, "stamp": _stamp(), "id": int(user["id"])},
        )
        if not enabled:
            conn.execute(
                text(
                    "UPDATE sessions SET revoked_at = :stamp "
                    "WHERE user_id = :id AND revoked_at IS NULL"
                ),
                {"stamp": revoked_at, "id": int(user["id"])},
            )
    invalidate_session_cache()
    record_audit(
        table="users",
        row_id=int(user["id"]),
        field="enabled" if enabled else "disabled",
        old_value="1" if user["enabled"] else "0",
        new_value=str(flag),
        reason=f"user {name} {'enabled' if enabled else 'disabled'} by {actor}"
        + ("" if enabled else " (live sessions revoked)"),
    )
    updated = get_user(name)
    return updated or {**user, "enabled": flag}


def set_user_role(username: str, *, role: str, actor: str = "cli") -> dict[str, Any]:
    """Change an account's role (``member`` ↔ ``admin``).

    Role is read back from the ``users`` row on every session resolve, so a
    fresh :func:`reset_session_cache` is enough — no forced logout, but a demoted
    admin loses admin routes on their very next request.

    Raises:
        LookupError: no such user.
        ValueError: unknown role (nothing is written).
    """
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    role_value = str(role or "").strip().lower()
    if role_value not in VALID_ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(VALID_ROLES)}")
    old_role = str(user.get("role") or "member")
    if old_role == role_value:
        return user
    stamp = _stamp()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET role = :role, updated_at = :stamp WHERE id = :id"),
            {"role": role_value, "stamp": stamp, "id": int(user["id"])},
        )
    # Role is embedded in cached session records — drop the whole cache so a
    # demotion takes effect on the next request instead of at TTL expiry.
    reset_session_cache()
    record_audit(
        table="users",
        row_id=int(user["id"]),
        field="role",
        old_value=old_role,
        new_value=role_value,
        reason=f"role of {name} changed by {actor}",
    )
    updated = get_user(name)
    return updated or {**user, "role": role_value, "updated_at": stamp}


def change_password(username: str, password: str, *, actor: str = "cli") -> dict[str, Any]:
    """Replace a password and revoke every live session for that user.

    Raising the password forces a fresh login — otherwise a leaked token would
    outlive the credential it was issued against.

    Raises:
        LookupError: no such user.
    """
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    digest = hash_password(password)
    revoked_at = _stamp()
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users SET password_hash = :digest, updated_at = :stamp WHERE id = :id"
            ),
            {"digest": digest, "stamp": revoked_at, "id": int(user["id"])},
        )
        conn.execute(
            text(
                "UPDATE sessions SET revoked_at = :stamp "
                "WHERE user_id = :id AND revoked_at IS NULL"
            ),
            {"stamp": revoked_at, "id": int(user["id"])},
        )
    invalidate_session_cache()
    record_audit(
        table="users",
        row_id=int(user["id"]),
        field="password",
        old_value=None,  # never the old hash
        new_value=None,  # never the new hash
        reason=f"password changed for {name} by {actor} (live sessions revoked)",
    )
    return {**user, "password_hash": digest}


def revoke_user_sessions(username: str, *, actor: str = "cli") -> dict[str, Any]:
    """Revoke every live session for a user (logout-everywhere).

    Returns ``{'username', 'revoked'}`` — a count, never a token.
    """
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    revoked_at = _stamp()
    with _get_manager().engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE sessions SET revoked_at = :stamp "
                "WHERE user_id = :id AND revoked_at IS NULL"
            ),
            {"stamp": revoked_at, "id": int(user["id"])},
        )
        revoked = int(result.rowcount or 0)
    invalidate_session_cache()
    record_audit(
        table="users",
        row_id=int(user["id"]),
        field="revoke_sessions",
        old_value=None,
        new_value=str(revoked),
        reason=f"{revoked} session(s) for {name} revoked by {actor}",
    )
    return {"username": name, "revoked": revoked}


def list_user_sessions(username: str) -> list[dict[str, Any]]:
    """Live sessions for a user — ids and expiry only, never a token hash."""
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    with _get_manager().engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, created_at, expires_at, revoked_at, last_used_at, created_by_ip "
                "FROM sessions WHERE user_id = :id ORDER BY id"
            ),
            {"id": int(user["id"])},
        ).fetchall()
    return [dict(row._mapping) for row in rows]


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoginRejected(Exception):
    """A rejected login. ``reason`` is for the audit row, never the response."""

    reason: str
    username: str = field(default="", repr=False)

    def __str__(self) -> str:
        return "invalid_credentials"


def verify_login(username: str, password: str) -> dict[str, Any]:
    """Verify a username + password, paying argon2 cost on every branch.

    Returns the user record on success.  Raises :class:`LoginRejected` for an
    unknown user, a wrong password or a disabled account — the three cases a
    caller must not be able to tell apart, which is why the dummy verify runs
    for an unknown user and the disabled case is checked *after* the password
    verified.

    CPU-bound: run it in a thread pool.
    """
    name = normalize_username(username)
    user = get_user(name)
    if user is None:
        # Equalize timing: an unknown user costs the same ~200ms as a real one.
        _burn_argon2_cycles(password)
        raise LoginRejected(reason=REASON_UNKNOWN_USER, username=name)

    if not verify_password_hash(str(user.get("password_hash") or ""), password):
        raise LoginRejected(reason=REASON_BAD_PASSWORD, username=name)

    if not user.get("enabled"):
        raise LoginRejected(reason=REASON_DISABLED, username=name)

    return user


def login(
    username: str,
    password: str,
    *,
    ip: str | None = None,
) -> dict[str, Any]:
    """Verify credentials and issue a session.

    Returns ``{'token', 'token_type', 'expires_at', 'user'}`` — ``token`` is the
    plaintext and is returned **once**; only its SHA-256 hash is stored.

    Raises:
        LoginRejected: credentials rejected (see :func:`verify_login`).

    CPU-bound (argon2): run it in a thread pool.
    """
    user = verify_login(username, password)
    issued = issue_session(int(user["id"]), ip=ip)
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET last_login = :stamp WHERE id = :id"),
            {"stamp": _stamp(), "id": int(user["id"])},
        )
    record_audit(
        table="sessions",
        row_id=int(issued["session_id"]),
        field="login",
        old_value=None,
        new_value=None,  # never the token
        reason=f"session issued for {user['username']}" + (f" from {ip}" if ip else ""),
    )
    return {
        "token": issued["token"],
        "token_type": "bearer",
        "expires_at": issued["expires_at"],
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "role": str(user.get("role") or "member").strip().lower(),
            "scopes": role_scopes(user.get("role")),
        },
    }


def issue_session(user_id: int, *, ip: str | None = None) -> dict[str, Any]:
    """Mint a session row and return the plaintext token once.

    Returns ``{'token', 'token_hash', 'session_id', 'expires_at'}``.  Callers
    must forward ``token`` to the client and keep everything else internal.
    """
    token = generate_session_token()
    token_hash = hash_token(token)
    ttl_hours = session_ttl_hours()
    expires_at = _stamp(datetime.now(timezone.utc) + timedelta(hours=ttl_hours))
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sessions (user_id, token_hash, expires_at, created_by_ip) "
                "VALUES (:user_id, :token_hash, :expires_at, :ip)"
            ),
            {
                "user_id": int(user_id),
                "token_hash": token_hash,
                "expires_at": expires_at,
                "ip": (ip or None),
            },
        )
        row = conn.execute(
            text("SELECT id FROM sessions WHERE token_hash = :token_hash"),
            {"token_hash": token_hash},
        ).first()
    if row is None:  # pragma: no cover - defensive
        raise ValueError("failed to persist the new session")
    return {
        "token": token,
        "token_hash": token_hash,
        "session_id": int(row[0]),
        "expires_at": expires_at,
    }


def revoke_session(token: str | None, *, actor: str = "api") -> bool:
    """Revoke **the presented session only**. Returns whether anything changed.

    Always safe to call with no token / an unknown token: it answers ``False``
    instead of raising, so ``POST /auth/logout`` can be idempotent and cannot
    be used to probe whether a token exists.
    """
    if not token:
        return False
    token_hash = hash_token(token)
    revoked_at = _stamp()
    with _get_manager().engine.begin() as conn:
        result = conn.execute(
            text("UPDATE sessions SET revoked_at = :stamp WHERE token_hash = :h AND revoked_at IS NULL"),
            {"stamp": revoked_at, "h": token_hash},
        )
        changed = int(result.rowcount or 0) > 0
        row = conn.execute(
            text("SELECT id, user_id FROM sessions WHERE token_hash = :h"), {"h": token_hash}
        ).first()
    invalidate_session_cache(token_hash)
    if row is not None and changed:
        record_audit(
            table="sessions",
            row_id=int(row[0]),
            field="logout",
            old_value=None,
            new_value=None,  # never the token
            reason=f"session revoked by {actor}",
        )
    return changed


# ---------------------------------------------------------------------------
# Resolution (called by the middleware after API-key resolution fails)
# ---------------------------------------------------------------------------


def _lookup_session(token_hash: str) -> dict[str, Any] | None:
    """Hash-index lookup + revoked/expiry checks, joined to the user.

    Returns the **auth record** (see :func:`record_from_user`), not the raw row.
    """
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT s.id AS session_id, s.user_id, s.expires_at AS session_expires_at, "
                "s.revoked_at, u.username, u.display_name, u.role, u.enabled "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = :h"
            ),
            {"h": token_hash},
        ).first()
    if row is None:
        return None
    found = dict(row._mapping)
    if found.get("revoked_at"):
        return None
    if str(found.get("session_expires_at") or "") <= _stamp():
        return None
    # A disabled account's sessions were revoked at disable time; this covers
    # the case where ``users.enabled`` was flipped by hand afterwards.
    if not found.get("enabled"):
        return None
    return record_from_user(found)


def resolve_session(token: str | None) -> dict[str, Any] | None:
    """Resolve a plaintext session token to an **auth record** (TTL-cached).

    The returned dict is the same shape the middleware publishes for an API
    key (so ``role_for`` / ``require_scope`` / ``require_role`` work unchanged)
    plus the session identity fields — ``auth_source='session'``,
    ``username``, ``display_name``, ``user_id``, ``session_id`` and
    ``expires_at``.

    Returns ``None`` for missing, unknown, expired or revoked tokens, and for
    anything that does not carry the :data:`SESSION_PREFIX` — so a machine API
    key can never be mistaken for a session.

    The cache is bounded and invalidated on every mutation (logout, revoke,
    disable, password change), so revocation is immediate in-process.
    """
    if not token or not is_session_token(token):
        return None
    computed = hash_token(token)
    now = time.monotonic()
    with _cache_lock:
        entry = _CACHE.get(computed)
        cached = entry[1] if (entry is not None and now < entry[0]) else None
    if entry is not None and now < entry[0]:
        if cached is not None:
            _touch_last_used(cached.get("session_id"))
        return cached

    try:
        record = _lookup_session(computed)
    except Exception:
        logger.exception("sessions lookup failed")
        return None

    with _cache_lock:
        if len(_CACHE) >= _CACHE_PRUNE_SIZE:
            _prune_cache(now)
        _CACHE[computed] = (now + _CACHE_TTL_S, record)
    if record is not None:
        _touch_last_used(record.get("session_id"))
    return record


def authenticate(token: str | None) -> dict[str, Any] | None:
    """Alias of :func:`resolve_session` for the middleware (explicit name)."""
    return resolve_session(token)
