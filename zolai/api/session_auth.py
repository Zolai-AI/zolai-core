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
- An account's role is ``member`` / ``admin`` / ``superadmin``, and
  :func:`set_user_role` is the **only** writer of ``users.role``.  It refuses,
  before touching the database, a role change by anyone below superadmin, a
  self-demotion, and the removal of the last enabled superadmin — so the first
  superadmin can only come from the CLI, which asserts its own rank out loud.
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
from datetime import timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..shared.clock import now as _clock_now
from ..shared.clock import parse as _clock_parse
from ..shared.clock import stamp as _clock_stamp

# The role hierarchy belongs to :mod:`zolai.api.rbac` — that is where every
# route reads it, so importing ``ROLES`` here (rather than re-listing the
# order) is what keeps "superadmin outranks admin" true in exactly one place.
# Safe at module level: rbac imports ``auth``, and neither imports this module.
from .rbac import ROLE_ADMIN, ROLE_SUPERADMIN
from .rbac import ROLES as _ROLE_ORDER

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
#: The hierarchy (``anonymous < member < admin < superadmin``) is owned by
#: :data:`zolai.api.rbac.ROLES`; this is the subset an *account* may hold.
VALID_ROLES: frozenset[str] = frozenset({"member", "admin", "superadmin"})

#: Scope sets — strict subsets of the frozen 33-action vocabulary in
#: :mod:`zolai.api.auth` (asserted by tests).  Deliberately no ``*``.
#:
#: The set is chosen by one rule, and ``TestTierScopeParity`` in
#: tests/test_role_hierarchy.py enforces it: **a role holds the action behind
#: every tool and every REST read its tier advertises.** A per-tool ``scope`` and
#: a ``require_scope`` action narrow against exactly these lists, so an action no
#: role holds is a feature only a hand-minted key can reach — a signed-in person
#: would be weaker than the bot on the same tier.  (That was true of
#: ``pos:read`` until this task: ``POST /api/v1/linguistics/pos`` and the
#: member-tier ``pos_tag`` tool were unreachable from any account.)
MEMBER_SCOPES: tuple[str, ...] = (
    "dataset:read",
    "rag:read",
    "catalog:read",
    "agent:read",
    "agent:run",
    "pos:read",
)
#: What an ``admin`` account holds.  ``role:manage`` is **not** here — granting
#: a role is a superadmin power, and an admin that can hand out roles is only
#: one misclick away from being able to hand out ``superadmin``.
#: ``pipeline:read`` / ``eval:read`` are the two learning-pipeline reads behind
#: the admin-tier ``learning_run_status`` and ``learning_version_history`` tools.
ADMIN_EXTRA_SCOPES: tuple[str, ...] = (
    "apikey:manage",
    "settings:read",
    "settings:write",
    "user:manage",
    "audit:read",
    "dataset:edit",
    "pos:review",
    "annotation:review",
    "quality:read",
    "pipeline:read",
    "eval:read",
)
SUPERADMIN_EXTRA_SCOPES: tuple[str, ...] = ("role:manage",)
ADMIN_SCOPES: tuple[str, ...] = MEMBER_SCOPES + ADMIN_EXTRA_SCOPES
SUPERADMIN_SCOPES: tuple[str, ...] = ADMIN_SCOPES + SUPERADMIN_EXTRA_SCOPES

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


def ensure_audit_log_table() -> dict[str, Any]:
    """Idempotently create ``data_audit_log`` — the trail :func:`record_audit` appends to.

    :func:`record_audit` deliberately does not create it: that would put a
    ``CREATE`` on every audited write, and the DDL has one home
    (:mod:`zolai.data.migrations`).  The API gets the table from its startup
    migrations; a **CLI-only flow on a brand-new store** had no such step, so
    ``zolai user create`` logged ``no such table: data_audit_log``, swallowed it,
    and dropped the audit row for the very first role — the one entry a later
    "who bootstrapped this deployment?" question reads.  Callers that write an
    audited change outside the server call this first.
    """
    from ..data.migrations import create_data_audit_log_table

    return create_data_audit_log_table(_get_manager())


def ensure_role_request_tables() -> dict[str, Any]:
    """Idempotently create ``role_requests`` (used by the router on first ask).

    Lives here rather than in ``role_requests_router`` for the same reason
    :func:`ensure_user_session_tables` does: the DDL has one home
    (:mod:`zolai.data.migrations`) and every caller — API, CLI, tests — reaches
    it through the service.
    """
    from ..data.migrations import create_role_request_tables

    return create_role_request_tables(_get_manager())


# ---------------------------------------------------------------------------
# Roles + scopes
# ---------------------------------------------------------------------------


def role_scopes(role: str | None) -> list[str]:
    """Scope set for a role. Unknown/empty role degrades to ``member``.

    Never raises: a hand-edited ``users.role`` must not be able to 500 the API.
    ``superadmin`` is ``admin`` plus ``role:manage`` — it is a *superset*, so
    anything an admin can reach a superadmin can reach.
    """
    normalized = str(role or "").strip().lower()
    if normalized == "superadmin":
        return list(SUPERADMIN_SCOPES)
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
                {"stamp": _clock_stamp(), "id": int(session_id)},
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
                    "changed_at": _clock_stamp(),
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


def _rank(role: str) -> int:
    """Position in the role hierarchy — :data:`zolai.api.rbac.ROLES` is the one
    ordering, so nothing here re-invents it."""
    return _ROLE_ORDER.index(role)


def _enabled_superadmin_count(conn: Any) -> int:
    """How many accounts can actually grant a role right now, on ``conn``.

    Runs inside the caller's transaction, so the last-superadmin check and the
    ``UPDATE`` cannot be separated by a concurrent write.
    """
    return int(
        conn.execute(
            text("SELECT COUNT(*) FROM users WHERE role = :role AND enabled = 1"),
            {"role": ROLE_SUPERADMIN},
        ).scalar_one()
    )


class RoleGuardRejected(ValueError):
    """A role/enablement invariant refused the write **before** anything changed.

    Subclasses :class:`ValueError` on purpose: a caller that has not been
    prepared for it still answers 400 rather than a 500, and either way the
    database is untouched.  Callers read ``reason`` and map it through
    :data:`GUARD_STATUS` for the code and :func:`guard_message` for the body.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: Guard ``reason`` → the HTTP status that answers it.  A rank or self
#: violation is a *permission* answer (403); the last superadmin is a *state*
#: conflict (409) — the caller could legally do it after promoting someone
#: else.  Two surfaces refuse with this exception (``admin_users_router`` and
#: the role-request queue), so the table lives here rather than in either
#: caller: they must not disagree on what the same violation costs.
GUARD_STATUS: dict[str, int] = {
    "role_change_requires_superadmin": 403,
    "target_requires_superadmin": 403,
    "self_role_change": 403,
    "self_disable": 403,
    "last_superadmin": 409,
}

#: Guard ``reason`` → the sentence an API caller reads.  Same reasoning as
#: :data:`GUARD_STATUS`: two HTTP surfaces (``admin_users_router`` and the
#: role-request queue) refuse these violations, and a body written per-surface
#: drifts.  Authored here, in prose that names no account, role or actor,
#: because the exception's own ``str()`` carries those — and an error body is
#: readable by whoever holds the credential that triggered it, which is not
#: necessarily the person the sentence is about.  The detailed variant stays on
#: the exception for logs and the audit row.
GUARD_MESSAGE: dict[str, str] = {
    "role_change_requires_superadmin": (
        "Applying a role needs role:manage, which is the superadmin tier."
    ),
    "target_requires_superadmin": (
        "That account ranks at admin or above, so only a superadmin may change it."
    ),
    "self_role_change": (
        "A superadmin cannot decide their own promotion — a peer must decide it."
    ),
    "self_disable": "An account cannot disable itself.",
    "last_superadmin": (
        "That account is the only enabled superadmin; promote a second one before removing it."
    ),
}

#: The fallback for a reason neither table knows: a future guard must never
#: answer 200, and must never echo the exception text either.
GUARD_FALLBACK_MESSAGE = "The role change was refused by an account-integrity guard."


def guard_message(reason: str) -> str:
    """The caller-facing sentence for a guard ``reason``."""
    return GUARD_MESSAGE.get(reason, GUARD_FALLBACK_MESSAGE)


def _checked_actor(
    actor_role: str, *, actor: str, actor_user_id: int | None
) -> tuple[str, str, int | None]:
    """Validate the *caller* half of a guarded write.

    ``actor_role`` is required, not optional: a caller that does not say who it
    is gets ``TypeError`` at the call site, which is a loud failure, while a
    default of "superadmin" would be a silent one.  The CLI is the only caller
    that asserts its own superadmin rank, and it does so out loud.
    """
    caller = str(actor_role or "").strip().lower()
    if caller not in VALID_ROLES:
        raise ValueError(
            f"unknown actor role {actor_role!r}; expected one of {sorted(VALID_ROLES)}"
        )
    return caller, str(actor or caller or "actor"), actor_user_id


def set_user_enabled(
    username: str,
    *,
    enabled: bool,
    actor: str = "cli",
    actor_role: str,
    actor_user_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Enable/disable an account — the only place that writes ``users.enabled``.

    Disabling revokes that user's live sessions in the same transaction, so a
    disabled account cannot keep using a token it already holds.

    Guards, all of them before any ``UPDATE``:

    - an actor below ``superadmin`` may not touch an ``admin`` or ``superadmin``
      account **in either direction** (they may enable/disable members — that is
      what ``user:manage`` is for);
    - nobody disables their **own** account (``actor_user_id``);
    - the **last enabled superadmin** is not disabled.

    Raises:
        LookupError: no such user.
        RoleGuardRejected: a guard refused; nothing was written.
    """
    name = normalize_username(username)
    caller, actor_name, actor_uid = _checked_actor(
        actor_role, actor=actor, actor_user_id=actor_user_id
    )
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    target_role = str(user.get("role") or "member").strip().lower()
    target_id = int(user["id"])

    if _rank(target_role) >= _rank(ROLE_ADMIN) and caller != ROLE_SUPERADMIN:
        raise RoleGuardRejected(
            "target_requires_superadmin",
            f"role '{caller}' may not {'enable' if enabled else 'disable'} an account "
            f"whose role is '{target_role}'",
        )
    if not enabled and actor_uid is not None and actor_uid == target_id:
        raise RoleGuardRejected(
            "self_disable", "an account may not disable itself; another admin must"
        )

    flag = 1 if enabled else 0
    revoked_at = None if enabled else _clock_stamp()
    with _get_manager().engine.begin() as conn:
        if not enabled and target_role == ROLE_SUPERADMIN and user.get("enabled"):
            if _enabled_superadmin_count(conn) <= 1:
                raise RoleGuardRejected(
                    "last_superadmin",
                    "this is the only enabled superadmin; promote another before disabling it",
                )
        conn.execute(
            text("UPDATE users SET enabled = :enabled, updated_at = :stamp WHERE id = :id"),
            {"enabled": flag, "stamp": _clock_stamp(), "id": target_id},
        )
        if not enabled:
            conn.execute(
                text(
                    "UPDATE sessions SET revoked_at = :stamp "
                    "WHERE user_id = :id AND revoked_at IS NULL"
                ),
                {"stamp": revoked_at, "id": target_id},
            )
    invalidate_session_cache()
    detail = f"user {name} {'enabled' if enabled else 'disabled'} by {actor_name} ({caller})"
    if not enabled:
        detail += " (live sessions revoked)"
    if reason:
        detail += f" — {reason}"
    record_audit(
        table="users",
        row_id=target_id,
        field="enabled" if enabled else "disabled",
        old_value="1" if user["enabled"] else "0",
        new_value=str(flag),
        reason=detail,
    )
    updated = get_user(name)
    return updated or {**user, "enabled": flag}


def set_user_role(
    username: str,
    *,
    role: str,
    actor: str = "cli",
    actor_role: str,
    actor_user_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Change an account's role — **the only writer of ``users.role``**.

    Three rules, checked before any ``UPDATE``, and each of them a
    :class:`RoleGuardRejected` rather than a partial write:

    1. **only a superadmin changes a role at all** (``role_change_requires_superadmin``).
       ``user:manage`` creates accounts, resets passwords and disables
       members; handing out ``admin`` has to be a higher power than that, or
       every admin key is a route to ``superadmin``;
    2. **nobody changes their own role** (``self_role_change``) — the guard
       that stops a session from promoting itself while it holds it;
    3. **the last enabled superadmin is not demoted** (``last_superadmin``) —
       counted on the same connection as the write.

    Rank-over-target (an actor may not touch a peer) is enforced where it can
    bite: :func:`set_user_enabled`, whose admin callers are below superadmin.
    Here rule 1 already caps the caller at superadmin, and a superadmin
    promoting a member *to* superadmin must stay possible.

    Role is read back from the ``users`` row on every session resolve, so a
    fresh :func:`reset_session_cache` is enough — no forced logout, but a
    demoted admin loses admin routes on their very next request.

    Raises:
        LookupError: no such user.
        ValueError: unknown role, or unknown actor role (nothing is written).
        RoleGuardRejected: a guard refused; nothing was written.
    """
    name = normalize_username(username)
    caller, actor_name, actor_uid = _checked_actor(
        actor_role, actor=actor, actor_user_id=actor_user_id
    )
    role_value = str(role or "").strip().lower()
    if role_value not in VALID_ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(VALID_ROLES)}")
    user = get_user(name)
    if user is None:
        raise LookupError(f"user '{name}' not found")
    old_role = str(user.get("role") or "member").strip().lower()
    target_id = int(user["id"])

    if caller != ROLE_SUPERADMIN:
        raise RoleGuardRejected(
            "role_change_requires_superadmin",
            f"changing a role needs role:manage (superadmin); the caller is '{caller}'",
        )
    if actor_uid is not None and actor_uid == target_id:
        raise RoleGuardRejected(
            "self_role_change", "an account may not change its own role; another superadmin must"
        )

    if old_role == role_value:
        return user
    stamp_str = _clock_stamp()
    with _get_manager().engine.begin() as conn:
        # Only an *enabled* superadmin is an operator, so the count that matters
        # is the one that can still grant a role.  Demoting a disabled account
        # stays possible — the CLI is the recovery path for that state.
        if (
            old_role == ROLE_SUPERADMIN
            and user.get("enabled")
            and _enabled_superadmin_count(conn) <= 1
        ):
            raise RoleGuardRejected(
                "last_superadmin",
                "this is the only enabled superadmin; promote another before demoting it",
            )
        conn.execute(
            text("UPDATE users SET role = :role, updated_at = :stamp WHERE id = :id"),
            {"role": role_value, "stamp": stamp_str, "id": target_id},
        )
    # Role is embedded in cached session records — drop the whole cache so a
    # demotion takes effect on the next request instead of at TTL expiry.
    reset_session_cache()
    detail = f"role of {name} '{old_role}' -> '{role_value}' by {actor_name} ({caller})"
    if reason:
        detail += f" — {reason}"
    record_audit(
        table="users",
        row_id=target_id,
        field="role",
        old_value=old_role,
        new_value=role_value,
        reason=detail,
    )
    updated = get_user(name)
    return updated or {**user, "role": role_value, "updated_at": stamp_str}


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
    revoked_at = _clock_stamp()
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
    revoked_at = _clock_stamp()
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
# Role requests — ask now, a superadmin decides later
# ---------------------------------------------------------------------------
#
# The queue exists because promotion is a decision made *about* an account, not
# a thing an account does to itself: :func:`set_user_role` refuses a
# self-role-change on purpose.  So the ask is stored, and a superadmin applies
# it **through that same writer** — this module never writes ``users.role``
# anywhere else, and the request row is only ever a record of what was asked.

#: Roles an account may ask for.  ``member`` is deliberately absent: every
#: account is created there, so asking for it is a no-op rather than a request.
REQUESTABLE_ROLES: tuple[str, ...] = (ROLE_ADMIN, ROLE_SUPERADMIN)

#: The ``role_requests.status`` domain (mirrors the table's CHECK constraint).
ROLE_REQUEST_STATUSES: frozenset[str] = frozenset({"pending", "approved", "rejected"})

_ROLE_REQUEST_COLUMNS = (
    "id, user_id, username, requested_role, reason, status, "
    "created_at, decided_by, decided_at, note"
)


@dataclass(slots=True)
class DuplicateRoleRequest(Exception):
    """A pending ask for that role already exists — the partial unique index spoke."""

    username: str = field(default="", repr=False)
    requested_role: str = ""

    def __str__(self) -> str:
        return f"duplicate_pending:{self.requested_role}"


@dataclass(slots=True)
class RoleRequestDecided(Exception):
    """The request was already approved or rejected; nothing changed."""

    request_id: int = 0
    status: str = ""

    def __str__(self) -> str:
        return f"already_decided:{self.status}"


def create_role_request(
    *,
    user_id: int,
    username: str,
    requested_role: str,
    reason: str,
) -> dict[str, Any]:
    """Record a pending role ask.  Returns the new row.

    Raises:
        ValueError: an unknown requested role (never stored — the CHECK would
            reject it anyway, but the caller deserves the sentence now).
        DuplicateRoleRequest: this account already has a pending ask for the
            same role.
    """
    role_value = str(requested_role or "").strip().lower()
    if role_value not in REQUESTABLE_ROLES:
        raise ValueError(
            f"role {requested_role!r} cannot be requested; expected one of {list(REQUESTABLE_ROLES)}"
        )
    name = normalize_username(username)
    row = {
        "user_id": int(user_id),
        "username": name,
        "requested_role": role_value,
        "reason": str(reason or "").strip()[:1000],
        "created_at": _clock_stamp(),
    }
    try:
        with _get_manager().engine.begin() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO role_requests "
                    "(user_id, username, requested_role, reason, status, created_at) "
                    "VALUES (:user_id, :username, :requested_role, :reason, 'pending', :created_at)"
                ),
                row,
            )
            new_id = int(result.lastrowid or 0)
    except IntegrityError as exc:
        # The only constraint this INSERT can trip is ux_role_requests_pending.
        logger.info("role request refused as duplicate: user=%s role=%s", name, role_value)
        raise DuplicateRoleRequest(username=name, requested_role=role_value) from exc
    return {**row, "id": new_id, "status": "pending", "decided_by": None, "decided_at": None, "note": None}


def get_role_request(request_id: int) -> dict[str, Any] | None:
    """One request by id, or ``None``."""
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT {_ROLE_REQUEST_COLUMNS} FROM role_requests WHERE id = :id"),
            {"id": int(request_id)},
        ).first()
    return dict(row._mapping) if row is not None else None


def list_role_requests(
    *,
    user_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Newest-first page of requests, optionally one account's own.

    ``user_id`` is the privacy control, not a nicety: passing it means the
    caller may only ever see its own rows, whichever route calls this.
    """
    where: list[str] = []
    params: dict[str, Any] = {"limit": max(1, int(limit))}
    if user_id is not None:
        where.append("user_id = :user_id")
        params["user_id"] = int(user_id)
    if status is not None:
        where.append("status = :status")
        params["status"] = str(status)
    sql = f"SELECT {_ROLE_REQUEST_COLUMNS} FROM role_requests"
    if where:
        sql += f" WHERE {' AND '.join(where)}"
    sql += " ORDER BY id DESC LIMIT :limit"
    with _get_manager().engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return [dict(row._mapping) for row in rows]


def decide_role_request(
    request_id: int,
    *,
    approve: bool,
    decided_by: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Move a pending request to ``approved`` / ``rejected`` and audit it.

    **Does not change any role.**  Approval applies the role through
    :func:`set_user_role` — the only writer — so an approval inherits every
    guard there (no self-change, no last-superadmin removal) instead of
    re-implementing them, and a refusal from that writer leaves this row
    ``pending`` for the operator to read.

    The status flip is conditional on ``status = 'pending'``, so two
    simultaneous decisions cannot both land: the loser raises
    :class:`RoleRequestDecided`.

    Raises:
        LookupError: no such request.
        RoleRequestDecided: already approved or rejected.
    """
    target = get_role_request(request_id)
    if target is None:
        raise LookupError(f"role request {request_id} not found")
    if target.get("status") != "pending":
        raise RoleRequestDecided(request_id=int(request_id), status=str(target.get("status")))

    new_status = "approved" if approve else "rejected"
    stamp_str = _clock_stamp()
    with _get_manager().engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE role_requests SET status = :status, decided_by = :decided_by, "
                "decided_at = :decided_at, note = :note "
                "WHERE id = :id AND status = 'pending'"
            ),
            {
                "status": new_status,
                "decided_by": str(decided_by or "")[:120],
                "decided_at": stamp_str,
                "note": (str(note).strip()[:1000] if note else None),
                "id": int(request_id),
            },
        )
        if int(result.rowcount or 0) == 0:
            raise RoleRequestDecided(request_id=int(request_id), status=new_status)
    record_audit(
        table="role_requests",
        row_id=int(request_id),
        field="status",
        old_value="pending",
        new_value=new_status,
        reason=(
            f"role request #{int(request_id)} for {target.get('username')} "
            f"('{target.get('requested_role')}') {new_status} by {decided_by}"
            + (f" — {str(note).strip()[:200]}" if note else "")
        ),
    )
    decided = get_role_request(request_id)
    return decided if decided is not None else {**target, "status": new_status}


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
            {"stamp": _clock_stamp(), "id": int(user["id"])},
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
    expires_at = _clock_stamp(_clock_now() + timedelta(hours=ttl_hours))
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
    revoked_at = _clock_stamp()
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
    if str(found.get("session_expires_at") or "") and _clock_parse(str(found["session_expires_at"])) <= _clock_now():
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
