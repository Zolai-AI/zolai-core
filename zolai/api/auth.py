"""API-key authentication service for the versioned surface (``/api/v1``).

Implements backlog **P0-1** / [ADR-014]: machine keys with SHA-256-at-rest
hashes, a frozen ``resource:action`` scope vocabulary shared with the RBAC
matrix, audit rows on issue/rotate/revoke, and a warn→enforce dual-accept
window controlled by ``ZOLAI_API_AUTH``.

Design notes:

- Plaintext key is generated as ``zolai_sk_<token_urlsafe(32)>`` and returned
  **once** at issue/rotate time; only ``key_hash`` (SHA-256 hex) and a short
  display ``key_prefix`` are stored.
- Verification is a hash-index lookup plus ``hmac.compare_digest`` (constant
  time on the compared value).
- A small in-process TTL cache keeps the hot path off SQLite; every mutation
  (create/rotate/revoke) invalidates it so revocation is immediate in-process
  (cross-process revocation converges within ``KEY_CACHE_TTL_S``).
- ``last_used_at`` writes are throttled to at most one UPDATE per key per
  minute.
- Auth-failure logging is rate limited per reason (structured, logger
  ``zolai.api.auth``) so a scan cannot flood the log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from ..config import config

logger = logging.getLogger("zolai.api.auth")

#: Plaintext key format — the prefix doubles as the revocable lookup handle.
API_KEY_PREFIX = "zolai_sk_"

#: Displayed portion of a key (never enough to reconstruct the secret).
PREFIX_LEN = 16

#: Positive/negative verification cache TTL (seconds).
KEY_CACHE_TTL_S = 10.0

#: Minimum spacing between ``last_used_at`` writes for one key (seconds).
LAST_USED_THROTTLE_S = 60.0

#: Max structured auth-failure log lines per reason per minute.
FAILURE_LOG_LIMIT = 10

#: Default rate limit (requests/minute/key) when ``ZOLAI_API_RATE_LIMIT_RPM``
#: is unset or invalid.
DEFAULT_RATE_LIMIT_RPM = 60

# ---------------------------------------------------------------------------
# Frozen action vocabulary — docs/admin/permissions.md §2 (30 actions).
# ---------------------------------------------------------------------------

VALID_ACTIONS: frozenset[str] = frozenset(
    {
        "dashboard:read",
        "dataset:read",
        "dataset:create",
        "dataset:edit",
        "dataset:run_quality",
        "dataset:validate",
        "dataset:publish",
        "dataset:deprecate",
        "source:read",
        "source:write",
        "pos:read",
        "pos:annotate",
        "pos:review",
        "pos:adjudicate",
        "annotation:read",
        "annotation:review",
        "quality:read",
        "quality:run",
        "quality:waive",
        "eval:read",
        "eval:run",
        "pipeline:read",
        "pipeline:run",
        "catalog:read",
        "audit:read",
        "user:manage",
        "role:manage",
        "apikey:manage",
        "settings:read",
        "settings:write",
    }
)

#: Resources covered by the frozen vocabulary (for ``resource:*`` sugar).
VALID_RESOURCES: frozenset[str] = frozenset(a.split(":", 1)[0] for a in VALID_ACTIONS)

#: Distinct action suffixes (for ``*:read``-style grant sugar).
VALID_SUFFIXES: frozenset[str] = frozenset(a.split(":", 1)[1] for a in VALID_ACTIONS)


# ---------------------------------------------------------------------------
# Mode / limit resolvers — env wins at call time so tests and ops can flip
# the dual-accept window without reimporting the process.
# ---------------------------------------------------------------------------


def api_auth_mode() -> str:
    """Return the active auth mode: ``warn`` (default), ``enforce``, ``off``."""
    raw = os.environ.get("ZOLAI_API_AUTH")
    if raw is None or not raw.strip():
        raw = str(getattr(config, "api_auth_mode", "warn"))
    mode = raw.strip().lower()
    return mode if mode in {"warn", "enforce", "off"} else "warn"


def api_rate_limit_rpm() -> int:
    """Return the per-key rate limit in requests/minute (min 1)."""
    raw = os.environ.get("ZOLAI_API_RATE_LIMIT_RPM")
    if raw is None or not raw.strip():
        raw = str(getattr(config, "api_rate_limit_rpm", DEFAULT_RATE_LIMIT_RPM))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = DEFAULT_RATE_LIMIT_RPM
    return max(1, value)


# ---------------------------------------------------------------------------
# Key primitives
# ---------------------------------------------------------------------------


def generate_api_key() -> str:
    """Mint a fresh plaintext key (shown once, never stored)."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(plain: str) -> str:
    """SHA-256 hex digest of a plaintext key — the only form at rest."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def derive_prefix(plain: str) -> str:
    """Short display prefix stored alongside the hash."""
    return plain[:PREFIX_LEN]


def validate_scopes(scopes: list[str]) -> list[str]:
    """Validate scopes against the frozen vocabulary.

    Accepts an exact action, ``*`` (all), ``<resource>:*`` or ``*:<action>``
    grant sugar (docs/admin/permissions.md §2).

    Raises:
        ValueError: on an empty list or any unknown resource/action.
    """
    if not scopes:
        raise ValueError("scopes must not be empty")
    cleaned: list[str] = []
    for raw in scopes:
        scope = str(raw).strip()
        if not scope:
            continue
        if scope == "*" or scope in VALID_ACTIONS:
            cleaned.append(scope)
            continue
        resource, sep, suffix = scope.partition(":")
        if not sep:
            raise ValueError(f"unknown scope '{raw}' — expected resource:action")
        if resource == "*" and suffix in VALID_SUFFIXES:
            cleaned.append(scope)
        elif suffix == "*" and resource in VALID_RESOURCES:
            cleaned.append(scope)
        else:
            raise ValueError(f"unknown scope '{raw}' — not in the frozen 30-action vocabulary")
    if not cleaned:
        raise ValueError("scopes must not be empty")
    # Deduplicate, keep order.
    return list(dict.fromkeys(cleaned))


def scope_allows(scopes: list[str], action: str) -> bool:
    """Whether a key holding ``scopes`` may perform ``action``."""
    if not action or action not in VALID_ACTIONS:
        return False
    if "*" in scopes:
        return True
    if action in scopes:
        return True
    resource, suffix = action.split(":", 1)
    return f"{resource}:*" in scopes or f"*:{suffix}" in scopes


# ---------------------------------------------------------------------------
# Manager accessor + table bootstrap (monkeypatch seam for tests)
# ---------------------------------------------------------------------------


def _get_manager() -> Any:
    """Resolve the DatabaseManager singleton (late import avoids cycles)."""
    from ..data.database import get_manager

    return get_manager()


def ensure_api_keys_table() -> dict[str, Any]:
    """Idempotently create ``api_keys`` (used by the CLI before any command)."""
    from ..data.migrations import create_api_keys_table

    return create_api_keys_table(_get_manager())


# ---------------------------------------------------------------------------
# Verification cache + throttled bookkeeping
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
#: key_hash -> (expires_monotonic, record | None)
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}

_used_lock = threading.Lock()
#: key id -> monotonic time of the last last_used_at write
_LAST_USED: dict[int, float] = {}

_fail_lock = threading.Lock()
#: reason -> (minute_epoch, count)
_FAIL_LOG: dict[str, tuple[int, int]] = {}


def invalidate_key_cache(key_hash: str | None = None) -> None:
    """Drop cached verifications (all, or one) after a mutation."""
    with _cache_lock:
        if key_hash is None:
            _CACHE.clear()
        else:
            _CACHE.pop(key_hash, None)


def reset_failure_log() -> None:
    """Reset the auth-failure log throttle (tests)."""
    with _fail_lock:
        _FAIL_LOG.clear()


def log_auth_failure(reason: str, path: str, key_prefix: str | None = None) -> None:
    """Structured, per-reason rate-limited auth-failure log line."""
    minute = int(time.time() // 60)
    with _fail_lock:
        current = _FAIL_LOG.get(reason)
        if current is not None and current[0] == minute:
            if current[1] >= FAILURE_LOG_LIMIT:
                _FAIL_LOG[reason] = (minute, current[1] + 1)
                return
            _FAIL_LOG[reason] = (minute, current[1] + 1)
        else:
            _FAIL_LOG[reason] = (minute, 1)
    logger.warning(
        "api_auth_failure: %s",
        {"reason": reason, "path": path, "key_prefix": key_prefix},
    )


def touch_last_used(record: dict[str, Any]) -> None:
    """Throttled ``last_used_at`` update (≤ 1 write/60s per key)."""
    key_id = record.get("id")
    if key_id is None:
        return
    now = time.monotonic()
    with _used_lock:
        last = _LAST_USED.get(int(key_id))
        if last is not None and now - last < LAST_USED_THROTTLE_S:
            return
        _LAST_USED[int(key_id)] = now
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_manager().engine.begin() as conn:
            conn.execute(
                text("UPDATE api_keys SET last_used_at = :stamp WHERE id = :id"),
                {"stamp": stamp, "id": int(key_id)},
            )
    except Exception:
        # Bookkeeping must never fail a request.
        logger.debug("last_used_at update skipped for key %s", key_id)


def _row_to_record(row: Any) -> dict[str, Any]:
    record = dict(row._mapping)
    try:
        record["scopes"] = json.loads(record.get("scopes") or "[]")
    except (TypeError, ValueError):
        record["scopes"] = []
    return record


def _lookup_by_hash(key_hash: str) -> dict[str, Any] | None:
    """Hash-index lookup + constant-time compare + expiry/revocation checks."""
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, key_prefix, key_hash, scopes, created_by, "
                "created_at, expires_at, last_used_at, revoked_at "
                "FROM api_keys WHERE key_hash = :h"
            ),
            {"h": key_hash},
        ).first()
    if row is None:
        return None
    record = _row_to_record(row)
    # Constant-time compare of the stored hash against the computed one.
    if not hmac.compare_digest(str(record.get("key_hash") or ""), key_hash):
        return None
    if record.get("revoked_at"):
        return None
    expires_at = record.get("expires_at")
    if expires_at and str(expires_at) <= datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"):
        return None
    return record


def resolve_key(plain: str | None) -> dict[str, Any] | None:
    """Resolve a plaintext key to an active record (TTL-cached).

    Returns ``None`` for missing, unknown, expired or revoked keys.
    """
    if not plain:
        return None
    computed = hash_key(plain)
    now = time.monotonic()
    with _cache_lock:
        entry = _CACHE.get(computed)
        if entry is not None and now < entry[0]:
            record = entry[1]
    if entry is not None and now < entry[0]:
        if record is not None:
            touch_last_used(record)
        return record

    try:
        record = _lookup_by_hash(computed)
    except Exception:
        logger.exception("api_keys lookup failed")
        return None

    with _cache_lock:
        _CACHE[computed] = (now + KEY_CACHE_TTL_S, record)
    if record is not None:
        touch_last_used(record)
    return record


# ---------------------------------------------------------------------------
# Audit helper — data_audit_log rows for issue / rotate / revoke
# ---------------------------------------------------------------------------


def record_audit(
    *,
    row_id: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
    reason: str,
    manager: Any = None,
) -> None:
    """Append an ``api_keys`` event to ``data_audit_log`` (never a secret)."""
    mgr = manager or _get_manager()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with mgr.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO data_audit_log "
                    "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
                    "VALUES ('api_keys', :row_id, :field, :old_value, :new_value, :changed_at, :reason)"
                ),
                {
                    "row_id": int(row_id),
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "changed_at": stamp,
                    "reason": reason,
                },
            )
    except Exception:
        # Never break the operation on an audit hiccup — but make it loud.
        logger.exception("data_audit_log write failed for api_keys row %s", row_id)


# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------


def create_api_key(
    *,
    name: str,
    scopes: list[str],
    created_by: str = "cli",
    expires_days: int | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Issue a new key. Returns the record plus ``plaintext`` (shown once)."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("name must not be empty")
    clean_scopes = validate_scopes(list(scopes))
    plain = generate_api_key()
    key_hash = hash_key(plain)
    prefix = derive_prefix(plain)
    expires_at = None
    if expires_days:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=int(expires_days))
        ).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "name": name,
        "key_prefix": prefix,
        "key_hash": key_hash,
        "scopes": json.dumps(clean_scopes),
        "created_by": str(created_by or "cli"),
        "expires_at": expires_at,
    }

    mgr = _get_manager()
    try:
        with mgr.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_keys "
                    "(name, key_prefix, key_hash, scopes, created_by, expires_at) "
                    "VALUES (:name, :key_prefix, :key_hash, :scopes, :created_by, :expires_at)"
                ),
                params,
            )
            row = conn.execute(
                text("SELECT * FROM api_keys WHERE key_hash = :h"), {"h": key_hash}
            ).first()
    except Exception as exc:
        message = str(exc)
        if "UNIQUE" in message.upper():
            raise ValueError(f"an API key named '{name}' already exists") from exc
        raise
    if row is None:  # pragma: no cover - defensive
        raise ValueError("failed to persist the new API key")

    record = _row_to_record(row)
    record_audit(
        row_id=int(record["id"]),
        field="create",
        old_value=None,
        new_value=prefix,
        reason=f"apikey issued by {actor or created_by}",
    )
    return {**record, "plaintext": plain}


def list_api_keys() -> list[dict[str, Any]]:
    """All key records (hashes only — no plaintext exists at rest)."""
    with _get_manager().engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, name, key_prefix, key_hash, scopes, created_by, created_at, "
                "expires_at, last_used_at, revoked_at FROM api_keys ORDER BY id"
            )
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_api_key(key_id: int) -> dict[str, Any] | None:
    """Fetch one key record by id (or ``None``)."""
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, key_prefix, key_hash, scopes, created_by, created_at, "
                "expires_at, last_used_at, revoked_at FROM api_keys WHERE id = :id"
            ),
            {"id": int(key_id)},
        ).first()
    return _row_to_record(row) if row is not None else None


def find_api_key(target: str) -> dict[str, Any] | None:
    """Look a key up by numeric id or by ``key_prefix``."""
    target = str(target).strip()
    if target.isdigit():
        return get_api_key(int(target))
    with _get_manager().engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, key_prefix, key_hash, scopes, created_by, created_at, "
                "expires_at, last_used_at, revoked_at FROM api_keys "
                "WHERE key_prefix = :p OR (key_prefix LIKE :like AND revoked_at IS NULL) "
                "ORDER BY id LIMIT 1"
            ),
            {"p": target, "like": f"{target}%"},
        ).first()
    return _row_to_record(row) if row is not None else None


def _unique_name(base: str) -> str:
    """Return ``base`` (or a suffix) that is free under the UNIQUE constraint."""
    existing = {k["name"] for k in list_api_keys()}
    if base not in existing:
        return base
    for n in range(2, 1000):
        candidate = f"{base}-rotated-{n}" if n > 2 else f"{base}-rotated"
        if candidate not in existing:
            return candidate
    raise ValueError("could not allocate a unique name for the rotated key")


def rotate_api_key(key_id: int, *, actor: str = "cli") -> dict[str, Any]:
    """Issue a replacement key and revoke the old one (issue-then-revoke).

    Returns ``old_id`` plus the new record with ``plaintext`` (shown once).
    """
    old = get_api_key(key_id)
    if old is None:
        raise LookupError(f"API key {key_id} not found")
    if old.get("revoked_at"):
        raise ValueError(f"API key {key_id} is already revoked")

    plain = generate_api_key()
    key_hash = hash_key(plain)
    prefix = derive_prefix(plain)
    new_name = _unique_name(f"{old['name']}")
    mgr = _get_manager()
    with mgr.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO api_keys "
                "(name, key_prefix, key_hash, scopes, created_by, expires_at) "
                "VALUES (:name, :key_prefix, :key_hash, :scopes, :created_by, :expires_at)"
            ),
            {
                "name": new_name,
                "key_prefix": prefix,
                "key_hash": key_hash,
                "scopes": json.dumps(old.get("scopes") or []),
                "created_by": old.get("created_by") or "cli",
                "expires_at": old.get("expires_at"),
            },
        )
        row = conn.execute(
            text("SELECT * FROM api_keys WHERE key_hash = :h"), {"h": key_hash}
        ).first()
        revoked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            text("UPDATE api_keys SET revoked_at = :revoked_at WHERE id = :id"),
            {"revoked_at": revoked_at, "id": int(key_id)},
        )
    if row is None:  # pragma: no cover - defensive
        raise ValueError("rotation failed to persist the replacement key")

    new_record = _row_to_record(row)
    invalidate_key_cache()
    record_audit(
        row_id=int(new_record["id"]),
        field="create",
        old_value=None,
        new_value=prefix,
        reason=f"apikey rotated in by {actor} (replaces id {key_id})",
    )
    record_audit(
        row_id=int(key_id),
        field="revoke",
        old_value=old.get("key_prefix"),
        new_value=f"rotated -> {prefix}",
        reason=f"apikey rotated out by {actor}",
    )
    return {
        **new_record,
        "old_id": int(key_id),
        "old_revoked_at": revoked_at,
        "plaintext": plain,
    }


def revoke_api_key(key_id: int, *, actor: str = "cli") -> dict[str, Any]:
    """Set ``revoked_at`` — the key is rejected from the very next request."""
    record = get_api_key(key_id)
    if record is None:
        raise LookupError(f"API key {key_id} not found")
    if record.get("revoked_at"):
        raise ValueError(f"API key {key_id} is already revoked")
    revoked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _get_manager().engine.begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET revoked_at = :revoked_at WHERE id = :id"),
            {"revoked_at": revoked_at, "id": int(key_id)},
        )
    invalidate_key_cache()
    record_audit(
        row_id=int(key_id),
        field="revoke",
        old_value=record.get("key_prefix"),
        new_value="revoked",
        reason=f"apikey revoked by {actor}",
    )
    return {**record, "revoked_at": revoked_at}


# ---------------------------------------------------------------------------
# FastAPI dependency — scope enforcement on admin routes
# ---------------------------------------------------------------------------


def require_scope(action: str) -> Any:
    """Route dependency enforcing a frozen ``resource:action`` scope.

    Auth itself is the middleware's job; this only checks scopes once a key
    is present.  In ``warn`` mode an absent key is dual-accepted (legacy
    posture), but a *presented* key without the scope is still denied.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"require_scope('{action}') is not in the frozen vocabulary")

    def dependency(request: Request) -> dict[str, Any]:
        record: dict[str, Any] | None = getattr(request.state, "api_key", None)
        if record is None:
            if api_auth_mode() == "enforce":
                raise HTTPException(
                    status_code=401,
                    detail={"error": "unauthorized", "reason": "missing_api_key"},
                )
            # warn/off: dual-accept — the middleware already logged the miss.
            return {}
        scopes = list(record.get("scopes") or [])
        if not scope_allows(scopes, action):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden",
                    "action": action,
                    "reason": "missing_scope",
                },
            )
        return record

    return dependency
