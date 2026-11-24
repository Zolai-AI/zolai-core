"""Append-only, redacted audit events for domain agent runs.

Events are stored in ``agent_run_events`` (additive). Secret-shaped values
are masked before persistence and before API/CLI return. This module never
logs raw secrets.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from ...shared.clock import stamp

logger = logging.getLogger(__name__)

# Conservative key/value patterns. Mask, never echo.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|cookie|session|bearer|private[_-]?key)",
    re.I,
)
_SECRET_VALUE_RES = [
    re.compile(r"\bsk-[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}\b", re.I),
    re.compile(r"\b-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _mask_string(value: str) -> str:
    out = value
    for rx in _SECRET_VALUE_RES:
        out = rx.sub("[REDACTED]", out)
    # Mask URL userinfo
    out = re.sub(r"(https?://)([^/\s@]+)@", r"\1[REDACTED]@", out, flags=re.I)
    return out


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secret-shaped keys/values."""
    if _depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                redacted[ks] = "[REDACTED]"
            else:
                redacted[ks] = redact(v, _depth=_depth + 1)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        if len(value) > 4000:
            value = value[:4000] + "...[TRUNCATED]"
        return _mask_string(value)
    return value


def ensure_agent_run_events_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa_text(
                """
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain_run_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    event_type TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
                )
                """
            )
        )
        conn.execute(
            sa_text(
                "CREATE INDEX IF NOT EXISTS ix_agent_run_events_run_seq ON agent_run_events(domain_run_id, seq)"
            )
        )


def append_event(
    engine: Engine,
    *,
    domain_run_id: int,
    seq: int,
    event_type: str,
    phase: str = "",
    tool_name: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one redacted event. Never mutates existing rows."""
    ensure_agent_run_events_table(engine)
    safe_payload = redact(payload or {})
    row = {
        "domain_run_id": int(domain_run_id),
        "seq": int(seq),
        "event_type": str(event_type),
        "phase": str(phase or ""),
        "tool_name": str(tool_name or ""),
        "payload_json": json.dumps(safe_payload, ensure_ascii=False),
        "created_at": stamp(),
    }
    with engine.begin() as conn:
        cursor = conn.execute(
            sa_text(
                "INSERT INTO agent_run_events ("
                "domain_run_id, seq, event_type, phase, tool_name, payload_json, created_at"
                ") VALUES ("
                ":domain_run_id, :seq, :event_type, :phase, :tool_name, :payload_json, :created_at"
                ")"
            ),
            row,
        )
        row_id = cursor.lastrowid
    return {"id": int(row_id), **row, "payload": safe_payload}


def list_events(engine: Engine, domain_run_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
    ensure_agent_run_events_table(engine)
    limit = max(1, min(int(limit or 500), 2000))
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text(
                "SELECT id, domain_run_id, seq, event_type, phase, tool_name, payload_json, created_at "
                "FROM agent_run_events WHERE domain_run_id = :rid ORDER BY seq ASC, id ASC LIMIT :lim"
            ),
            {"rid": int(domain_run_id), "lim": limit},
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload_raw = r[6]
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
        except Exception:
            payload = {"_raw": str(payload_raw)[:500]}
        out.append(
            {
                "id": int(r[0]),
                "domain_run_id": int(r[1]),
                "seq": int(r[2]),
                "event_type": str(r[3]),
                "phase": str(r[4] or ""),
                "tool_name": str(r[5] or ""),
                "payload": redact(payload),
                "created_at": str(r[7] or ""),
            }
        )
    return out
