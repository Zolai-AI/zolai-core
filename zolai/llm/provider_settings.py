"""Admin-configurable AI provider rows — CRUD, masking, secrets, resolution.

Python port of ``pcore-assistant`` ``services/ai.ts`` + ``services/assistant-ai.ts``
storage side (P1 §A):

- **Reads** the ``ai_providers`` table (seeded once by
  :func:`zolai.llm.catalog.seed_catalog`); there is no boot cache — every
  request re-reads so an admin enable/disable takes effect immediately.
- **Secrets never leave this module in plaintext.**  ``api_key_ref`` holds
  ``env:NAME`` (nothing at rest), ``enc:v1:<payload>`` (optional Fernet) or
  ``NULL``.  :func:`mask_secret_ref` is the only shape the API may return.
- **Stable error codes** (:data:`ASSISTANT_*`) — callers branch on ``code``,
  never on message text.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Stable machine-readable error codes (callers branch on these) ────────────

ASSISTANT_PROVIDER_NOT_FOUND = "ASSISTANT_PROVIDER_NOT_FOUND"
ASSISTANT_MODEL_NOT_SELECTED = "ASSISTANT_MODEL_NOT_SELECTED"
ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER = "ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER"

#: Per-request ``provider`` / ``model`` overrides (assistant chat + agent runs).
#: Callers branch on ``code`` — never on message text.
PROVIDER_UNKNOWN = "PROVIDER_UNKNOWN"
PROVIDER_INACTIVE = "PROVIDER_INACTIVE"
PROVIDER_MODEL_UNKNOWN = "PROVIDER_MODEL_UNKNOWN"

#: ``env:`` reference prefix — nothing secret is stored, only the var name.
ENV_REF_PREFIX = "env:"

#: Encrypted reference prefix (optional ``cryptography`` extra).
ENC_REF_PREFIX = "enc:v1:"

#: Env var holding the Fernet key for ``enc:v1:`` payloads.
SECRET_KEY_ENV = "ZOLAI_PROVIDER_SECRET_KEY"

#: Rows listed in this order (stable for the settings table).
ORDER_BY = "catalog_id"


class ProviderError(Exception):
    """Provider-settings failure carrying a stable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── Manager seam (monkeypatchable in tests, same pattern as ``zolai.api.auth``) ──


def _get_manager() -> Any:
    from ..data.database import get_manager

    return get_manager()


def ensure_provider_tables() -> dict[str, Any]:
    from ..data.migrations import create_ai_provider_tables

    return create_ai_provider_tables(_get_manager())


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_dict(row: Any) -> dict[str, Any]:
    """Plain dict copy of a SQLAlchemy Row (label-keyed)."""
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}  # pragma: no cover
    return dict(row)  # pragma: no cover


def list_provider_rows(manager: Any = None) -> list[dict[str, Any]]:
    """Every provider row, stable order (admin settings table)."""
    mgr = manager or _get_manager()
    with mgr.engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT * FROM ai_providers ORDER BY {ORDER_BY}")
        ).fetchall()
    return [_row_dict(r) for r in rows]


def get_provider_row(catalog_id: str, manager: Any = None) -> dict[str, Any] | None:
    """One row by its stable ``catalog_id`` (never by display name)."""
    mgr = manager or _get_manager()
    with mgr.engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM ai_providers WHERE catalog_id = :cid"),
            {"cid": str(catalog_id)},
        ).fetchone()
    return _row_dict(row) if row is not None else None


def get_enabled_provider_rows(manager: Any = None) -> list[dict[str, Any]]:
    """Enabled rows only — the candidate set for :func:`zolai.llm.adapter.pick_provider`."""
    mgr = manager or _get_manager()
    with mgr.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM ai_providers WHERE enabled = 1 ORDER BY catalog_id")
        ).fetchall()
    return [_row_dict(r) for r in rows]


# ── Secrets: mask for reads, resolve for calls ───────────────────────────────


def mask_secret_ref(ref: str | None) -> dict[str, Any]:
    """Public shape of ``api_key_ref`` — **never** the secret itself.

    Returns ``{"mode", "ref_masked", "configured"}`` where ``mode`` is
    ``env`` | ``encrypted`` | ``none``.
    """
    raw = (ref or "").strip()
    if not raw:
        return {"mode": "none", "ref_masked": "", "configured": False}
    if raw.startswith(ENV_REF_PREFIX):
        name = raw[len(ENV_REF_PREFIX):]
        return {
            "mode": "env",
            # Show the variable name (ops need it) but not its value.
            "ref_masked": f"env:{name}",
            "configured": bool(os.environ.get(name, "").strip()),
        }
    if raw.startswith(ENC_REF_PREFIX):
        tail = raw[len(ENC_REF_PREFIX):]
        return {
            "mode": "encrypted",
            "ref_masked": f"enc:v1:{'*' * 8}{tail[-4:] if len(tail) > 4 else ''}",
            "configured": bool(tail),
        }
    # Unknown/legacy shape: never echo it back.
    return {"mode": "encrypted", "ref_masked": "enc:v1:********", "configured": True}


def _decrypt(payload: str) -> str:
    """Decrypt an ``enc:v1:`` payload (optional dependency, fail closed)."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning("enc:v1: secret present but 'cryptography' is not installed")
        return ""
    key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if not key:
        logger.warning("enc:v1: secret present but %s is unset", SECRET_KEY_ENV)
        return ""
    try:
        return Fernet(key.encode("utf-8")).decrypt(payload.encode("utf-8")).decode("utf-8")
    except Exception:
        logger.exception("enc:v1: secret decryption failed")
        return ""


def read_secret_ref(row: dict[str, Any]) -> str:
    """Plaintext key for ``row`` (``""`` when absent or unresolvable)."""
    ref = str(row.get("api_key_ref") or "")
    if ref.startswith(ENV_REF_PREFIX):
        return os.environ.get(ref[len(ENV_REF_PREFIX):], "").strip()
    if ref.startswith(ENC_REF_PREFIX):
        return _decrypt(ref[len(ENC_REF_PREFIX):])
    return ""


def read_catalog_env_fallback(entry: Any) -> str:
    """First of the catalog entry's ``env_keys`` that holds a value."""
    for name in getattr(entry, "env_keys", ()) or ():
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def encode_secret_ref(plaintext: str, manager: Any = None) -> str:
    """Encode a pasted secret for storage (``enc:v1:`` when possible).

    Raises:
        ProviderError: ``SECRET_ENCRYPTION_UNAVAILABLE`` when neither
        ``cryptography`` nor ``ZOLAI_PROVIDER_SECRET_KEY`` is available — the
        caller must use an ``env:NAME`` reference instead (no plaintext at rest).
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ProviderError(
            "SECRET_ENCRYPTION_UNAVAILABLE",
            "the optional 'cryptography' extra is not installed — store the key as an "
            "env: reference (secret_env) instead of a pasted value",
        ) from exc
    key = os.environ.get(SECRET_KEY_ENV, "").strip()
    if not key:
        raise ProviderError(
            "SECRET_ENCRYPTION_UNAVAILABLE",
            f"set {SECRET_KEY_ENV} (Fernet key) to store pasted secrets, or use an "
            "env: reference instead",
        )
    token = Fernet(key.encode("utf-8")).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return ENC_REF_PREFIX + token


# ── Audit (never logs a secret) ──────────────────────────────────────────────


def _audit(catalog_id: str, field: str, old: str | None, new: str | None, reason: str) -> None:
    """Append an ``ai_providers`` event to ``data_audit_log`` (never a secret)."""
    mgr = _get_manager()
    stamp = _utc_now()
    try:
        with mgr.engine.begin() as conn:
            row = conn.execute(
                text("SELECT id FROM ai_providers WHERE catalog_id = :cid"),
                {"cid": catalog_id},
            ).fetchone()
            row_id = int(row[0]) if row is not None else 0
            conn.execute(
                text(
                    "INSERT INTO data_audit_log "
                    "(table_name, row_id, field, old_value, new_value, changed_at, reason) "
                    "VALUES ('ai_providers', :row_id, :field, :old_value, :new_value, "
                    ":changed_at, :reason)"
                ),
                {
                    "row_id": row_id,
                    "field": field,
                    "old_value": old,
                    "new_value": new,
                    "changed_at": stamp,
                    "reason": reason,
                },
            )
    except Exception:
        logger.exception("data_audit_log write failed for ai_providers %s", catalog_id)


# ── CRUD ─────────────────────────────────────────────────────────────────────

#: Fields an admin may PUT (display + behaviour; ``catalog_id``/``adapter`` are fixed).
EDITABLE_FIELDS: tuple[str, ...] = (
    "name",
    "base_url",
    "models",
    "selected_model",
    "enabled",
    "tier",
    "timeout_s",
)


def upsert_provider(
    catalog_id: str,
    *,
    fields: dict[str, Any],
    secret: str | None = None,
    secret_env: str | None = None,
    clear_secret: bool = False,
    manager: Any = None,
) -> dict[str, Any]:
    """Apply an admin edit to one row.

    ``catalog_id`` is immutable; unknown ids raise ``LookupError`` (the API
    turns that into a real 404 — no silently created rows, so a typo cannot
    invent a provider).

    Secret handling: ``secret_env`` (an env var *name*) wins and stores
    ``env:NAME``; ``secret`` stores ``enc:v1:`` (400-class error when
    unavailable); ``clear_secret`` stores NULL.  Omitting all three keeps the
    previous reference.

    Raises:
        LookupError: unknown ``catalog_id``.
        ProviderError: bad/immutable field or unavailable encryption.
    """
    mgr = manager or _get_manager()
    current = get_provider_row(catalog_id, mgr)
    if current is None:
        raise LookupError(f"unknown provider {catalog_id!r}")

    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in EDITABLE_FIELDS:
            raise ProviderError("FIELD_IMMUTABLE", f"field {key!r} cannot be edited")
        if value is None:
            continue
        if key == "models":
            updates["models"] = json.dumps([str(m) for m in value])
        elif key == "enabled":
            updates["enabled"] = 1 if value else 0
        elif key == "timeout_s":
            updates["timeout_s"] = max(1, min(600, int(value)))
        else:
            updates[key] = str(value)

    if clear_secret:
        updates["api_key_ref"] = None
    elif secret_env:
        name = str(secret_env).strip()
        if name.startswith(ENV_REF_PREFIX):
            name = name[len(ENV_REF_PREFIX):]
        if not name or not name.replace("_", "").isalnum():
            raise ProviderError(
                "SECRET_ENV_INVALID", "secret_env must be a bare environment variable name"
            )
        updates["api_key_ref"] = f"{ENV_REF_PREFIX}{name}"
    elif secret:
        updates["api_key_ref"] = encode_secret_ref(str(secret), mgr)

    if not updates:
        return dict(current)

    updates["updated_at"] = _utc_now()
    assignments = ", ".join(f"{k} = :{k}" for k in updates)
    params = {**updates, "cid": catalog_id}
    try:
        with mgr.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE ai_providers SET {assignments} WHERE catalog_id = :cid"),
                params,
            )
    except Exception as exc:  # pragma: no cover - defensive
        raise ProviderError("PROVIDER_UPDATE_FAILED", str(exc)) from exc

    for key in updates:
        if key == "updated_at":
            continue
        old = current.get(key)
        new = updates[key]
        if key == "api_key_ref":
            # Audit the *shape* only — never a plaintext or encrypted payload.
            old_s = mask_secret_ref(str(old) if old else None)["ref_masked"] or "none"
            new_s = mask_secret_ref(str(new) if new else None)["ref_masked"] or "none"
            _audit(catalog_id, "secret", old_s, new_s, "settings:write")
        elif str(old) != str(new):
            _audit(catalog_id, key, str(old), str(new), "settings:write")

    return dict(get_provider_row(catalog_id, mgr) or current)


def activate_provider(catalog_id: str, manager: Any = None) -> dict[str, Any]:
    """Make ``catalog_id`` the single global active row (clears the others).

    Raises:
        LookupError: unknown id.
    """
    mgr = manager or _get_manager()
    if get_provider_row(catalog_id, mgr) is None:
        raise LookupError(f"unknown provider {catalog_id!r}")
    now = _utc_now()
    with mgr.engine.begin() as conn:
        conn.execute(text("UPDATE ai_providers SET is_active = 0"))
        conn.execute(
            text(
                "UPDATE ai_providers SET is_active = 1, updated_at = :now "
                "WHERE catalog_id = :cid"
            ),
            {"cid": catalog_id, "now": now},
        )
    _audit(catalog_id, "is_active", "0", "1", "settings:write activate")
    return dict(get_provider_row(catalog_id, mgr) or {})


def delete_provider(catalog_id: str, manager: Any = None) -> None:
    """Delete a ``custom`` row only — catalog rows are permanent (rename, don't drop).

    Raises:
        LookupError: unknown id.
        ProviderError: ``DELETE_FORBIDDEN`` for a seeded catalog row.
    """
    from .catalog import find_catalog

    mgr = manager or _get_manager()
    row = get_provider_row(catalog_id, mgr)
    if row is None:
        raise LookupError(f"unknown provider {catalog_id!r}")
    if find_catalog(catalog_id) is not None:
        raise ProviderError(
            "DELETE_FORBIDDEN",
            f"{catalog_id!r} is a built-in catalog row — disable it instead of deleting it",
        )
    with mgr.engine.begin() as conn:
        conn.execute(text("DELETE FROM ai_providers WHERE catalog_id = :cid"), {"cid": catalog_id})
    _audit(catalog_id, "row", "present", "deleted", "settings:write delete")


# ── Test connection (explicit admin action — not gated by ZOLAI_ENGINE_MODE) ──


def test_connection(catalog_id: str, *, timeout_s: float = 15.0, manager: Any = None) -> dict[str, Any]:
    """1-token probe against the row's endpoint.

    Returns ``{ok, status, latency_ms, error}``.  This is an *explicit* admin
    action (a "Test connection" button), so it is not routed through
    ``llm_allowed()`` — the engine-mode gate belongs to autonomous call sites
    (agent synthesis, assistant chat).
    """
    from . import adapter as adapter_mod

    row = get_provider_row(catalog_id, manager)
    if row is None:
        raise LookupError(f"unknown provider {catalog_id!r}")
    if not row.get("enabled", 1):
        return {"ok": False, "status": None, "latency_ms": 0.0, "error": "provider_disabled"}
    try:
        model = adapter_mod.resolve_model(row)
    except adapter_mod.ProviderError as exc:
        return {"ok": False, "status": None, "latency_ms": 0.0, "error": f"{exc.code}: {exc.message}"}
    try:
        result = adapter_mod.chat(
            row, model, [{"role": "user", "content": "ping"}], timeout_s=timeout_s
        )
    except adapter_mod.ProviderError as exc:
        return {"ok": False, "status": None, "latency_ms": 0.0, "error": f"{exc.code}: {exc.message}"}
    return {
        "ok": True,
        "status": result.get("status"),
        "latency_ms": result.get("latency_ms", 0.0),
        "error": None,
    }


# ── Assistant pin → provider resolution (assistant-ai.ts) ────────────────────


def resolve_assistant_ai(assistant: str, manager: Any = None) -> dict[str, Any]:
    """Resolve ``assistant`` ('public'|'admin') to ``{row, model, catalog_id}``.

    Per-assistant pin (``assistant_ai_pins``) → global active row and its
    ``selected_model`` (fallback: the row's first model).  Never guesses a
    model id that the row does not list.
    """
    from . import adapter as adapter_mod
    from .catalog import find_catalog

    mgr = manager or _get_manager()

    pin: dict[str, Any] | None = None
    try:
        with mgr.engine.connect() as conn:
            pin_row = conn.execute(
                text(
                    "SELECT assistant, catalog_id, model FROM assistant_ai_pins "
                    "WHERE assistant = :a"
                ),
                {"a": str(assistant)},
            ).fetchone()
        if pin_row is not None:
            pin = _row_dict(pin_row)
    except Exception:
        logger.exception("assistant_ai_pins read failed")

    catalog_id = str((pin or {}).get("catalog_id") or "").strip()
    if catalog_id:
        row = get_provider_row(catalog_id, mgr)
        if row is None:
            raise ProviderError(
                ASSISTANT_PROVIDER_NOT_FOUND,
                f"assistant {assistant!r} is pinned to {catalog_id!r}, which is not a provider row",
            )
        pinned_model = str((pin or {}).get("model") or "").strip()
        if pinned_model:
            models = adapter_mod._models(row)
            if pinned_model not in models:
                raise ProviderError(
                    ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER,
                    f"{pinned_model!r} is not in the model list of {catalog_id!r}",
                )
            return {"row": row, "model": pinned_model, "catalog_id": catalog_id}
        model = str(row.get("selected_model") or "").strip()
        if model:
            return {"row": row, "model": model, "catalog_id": catalog_id}
        fallback = adapter_mod._models(row)
        if fallback:
            return {"row": row, "model": fallback[0], "catalog_id": catalog_id}
        raise ProviderError(
            ASSISTANT_MODEL_NOT_SELECTED,
            f"provider {catalog_id!r} has no model selected — choose one in Admin → Settings",
        )

    # No pin → global active row (same preference order as pick_provider).
    row = adapter_mod.pick_provider(mgr)
    active_id = str(row.get("catalog_id") or "")
    model = str(row.get("selected_model") or "").strip()
    if not model:
        fallback = adapter_mod._models(row)
        if not fallback:
            raise ProviderError(
                ASSISTANT_MODEL_NOT_SELECTED,
                f"provider {active_id!r} has no model selected — choose one in Admin → Settings",
            )
        model = fallback[0]
    entry = find_catalog(active_id)
    if entry is not None:
        models = adapter_mod._models(row)
        if models and model not in models:
            raise ProviderError(
                ASSISTANT_MODEL_UNKNOWN_FOR_PROVIDER,
                f"{model!r} is not in the model list of {active_id!r}",
            )
    return {"row": row, "model": model, "catalog_id": active_id}


def parse_models(raw: Any) -> list[str]:
    """``models`` column (JSON string or list) → ``list[str]`` (never raises)."""
    if isinstance(raw, list):
        return [str(m) for m in raw]
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(m) for m in parsed] if isinstance(parsed, list) else []


def resolve_selection(
    provider: str | None = None,
    model: str | None = None,
    *,
    assistant: str | None = None,
    manager: Any = None,
) -> dict[str, Any]:
    """Validate an optional per-request ``provider`` / ``model`` (Phase B §9).

    Rules:

    - ``provider`` given → the row must exist (:data:`PROVIDER_UNKNOWN`) and be
      enabled (:data:`PROVIDER_INACTIVE`) — a disabled row is never selectable.
    - ``model`` given → it must be listed on the chosen row
      (:data:`PROVIDER_MODEL_UNKNOWN`).
    - neither given → the assistant's pin (or the global active row) plus its
      default model — identical to a request that never asked for an override.

    Returns ``{"row", "model", "catalog_id"}``.  Reads the DB only; **no socket
    is opened here**, so callers can validate an override in ``rule`` mode.

    Raises:
        ProviderError: with one of the stable codes above (or the adapter's
            ``NO_ACTIVE_PROVIDER`` / ``MODEL_NOT_CONFIGURED``).
    """
    from . import adapter as adapter_mod

    mgr = manager or _get_manager()

    if not provider and not model:
        if assistant:
            resolved = resolve_assistant_ai(assistant, mgr)
            return {
                "row": resolved["row"],
                "model": resolved["model"],
                "catalog_id": resolved["catalog_id"],
            }
        row = adapter_mod.pick_provider(mgr)
        return {
            "row": row,
            "model": adapter_mod.resolve_model(row),
            "catalog_id": str(row.get("catalog_id") or ""),
        }

    if provider:
        row = get_provider_row(str(provider), mgr)
        if row is None:
            raise ProviderError(PROVIDER_UNKNOWN, f"provider {provider!r} is not a catalog row")
        if not int(row.get("enabled") or 0):
            raise ProviderError(PROVIDER_INACTIVE, f"provider {provider!r} is disabled")
    elif assistant:
        row = resolve_assistant_ai(assistant, mgr)["row"]
    else:
        row = adapter_mod.pick_provider(mgr)

    catalog_id = str(row.get("catalog_id") or "")
    if model:
        wanted = str(model).strip()
        if wanted not in adapter_mod._models(row):
            raise ProviderError(
                PROVIDER_MODEL_UNKNOWN,
                f"{wanted!r} is not in the model list of provider {catalog_id!r}",
            )
        model_id = wanted
    else:
        model_id = adapter_mod.resolve_model(row)
    return {"row": row, "model": model_id, "catalog_id": catalog_id}
