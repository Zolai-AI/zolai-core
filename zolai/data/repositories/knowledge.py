"""Knowledge repositories — claims, hypotheses, version snapshots (Phase 1 §36).

Wraps the three new Phase 1 tables with the ``BaseRepository`` machinery:
audit rows in ``data_audit_log`` and optimistic locking via the row ``version``
counter (``row_version`` on ``knowledge_versions``, whose ``version`` column is
the unique TEXT release identifier instead).

``ClaimRepository`` enforces the evidence gate: a claim moving to
``SUPPORTED``/``VERIFIED`` must have linked ``claim_evidence`` rows — links are
written in the same transaction as the claim itself.

All writes go through these repositories (no LLM→canonical direct writes).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.contracts import (
    GATED_STATUSES,
    EvidenceGateError,
    KnowledgeStatus,
)

from .base import BaseRepository, OptimisticLockError

__all__ = [
    "ClaimRepository",
    "HypothesisRepository",
    "KnowledgeVersionRepository",
    "OptimisticLockError",
]


def _parse_id_list(value: Any) -> list[int]:
    """Normalize an id-list column (JSON string | list | None) → ``list[int]``."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [int(v) for v in value]


def _dump_id_list(value: Any) -> str:
    """Normalize an incoming id list to the stored JSON string."""
    return json.dumps(_parse_id_list(value))


def _status_value(value: Any, default: str = KnowledgeStatus.OBSERVED.value) -> str:
    """Normalize an incoming status (enum | str) → plain value."""
    if value is None:
        return default
    return KnowledgeStatus(value).value


def _storage_view(repo: BaseRepository, data: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only real table columns (drops contract view-only fields) and
    strip an explicit ``id=None`` so SQLite autoassigns the key."""
    columns = repo.table.columns
    out = {k: v for k, v in data.items() if k in columns}
    if out.get("id") is None:
        out.pop("id", None)
    return out


class ClaimRepository(BaseRepository):
    """``knowledge_claims`` + ``claim_evidence`` with the evidence gate."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "knowledge_claims")

    # -- gate helpers -------------------------------------------------------
    @staticmethod
    def _require_evidence(status: Any, evidence_ids: list[int], context: str) -> None:
        """Raise ``EvidenceGateError`` when a gated status has no evidence."""
        resolved = _status_value(status)
        if resolved in {s.value for s in GATED_STATUSES} and not evidence_ids:
            raise EvidenceGateError(
                f"{context}: status {resolved} requires at least one linked "
                "evidence row (evidence before confidence, confidence before status)"
            )

    def _normalize(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Copy ``data`` with enum/JSON columns normalized for storage.

        Keys that are absent stay absent (server defaults / caller-supplied
        status are never silently rewritten).
        """
        out = _storage_view(self, data)
        if "status" in out:
            out["status"] = _status_value(out["status"])
        if "evidence_ids" in out:
            out["evidence_ids"] = _dump_id_list(out["evidence_ids"])
        if "source_ids" in out:
            out["source_ids"] = _dump_id_list(out["source_ids"])
        return out

    # -- evidence links -----------------------------------------------------
    @staticmethod
    def _insert_evidence_links(
        conn: Any,
        claim_id: int,
        evidence_ids: Iterable[int],
        roles: Mapping[Any, str] | None = None,
    ) -> list[int]:
        """Insert ``claim_evidence`` rows (deduplicated, ordered).

        ``created_at`` is set explicitly: the raw SQL path must work whether
        the table came from the ORM (Python-side default only) or from the
        migration DDL (``DEFAULT datetime('now')``).
        """
        roles = {int(k): str(v) for k, v in (roles or {}).items()}
        now = datetime.now(timezone.utc).isoformat()
        inserted: list[int] = []
        for evidence_id in dict.fromkeys(evidence_ids):
            conn.execute(
                text(
                    "INSERT INTO claim_evidence (claim_id, evidence_id, role, created_at) "
                    "VALUES (:claim_id, :evidence_id, :role, :created_at)"
                ),
                {
                    "claim_id": claim_id,
                    "evidence_id": int(evidence_id),
                    "role": roles.get(int(evidence_id), "supports"),
                    "created_at": now,
                },
            )
            inserted.append(int(evidence_id))
        return inserted

    # -- CRUD with gate -----------------------------------------------------
    def create(self, data: dict[str, Any], user: str = "system") -> int:
        """Insert a claim (with evidence links) + audit row in one transaction.

        Raises:
            EvidenceGateError: ``SUPPORTED``/``VERIFIED`` without evidence ids.
            IntegrityError: an ``evidence_id`` that has no ``foundation_evidence``
                row (foreign key) or a duplicate S/P/O claim (expression-unique).
        """
        roles = dict(data.get("evidence_roles") or {})
        payload = self._normalize({k: v for k, v in data.items() if k != "evidence_roles"})
        evidence_ids = _parse_id_list(payload.get("evidence_ids"))
        self._require_evidence(payload.get("status"), evidence_ids, "claim create")

        with self._engine.begin() as conn:
            result = conn.execute(self.table.insert(), payload)
            entity_id = result.inserted_primary_key[0]
            self._insert_evidence_links(conn, entity_id, evidence_ids, roles)
            self._log_audit(
                conn,
                entity_id,
                "create",
                None,
                json.dumps(payload, ensure_ascii=False),
                user,
            )
            if self._version_column in self.table.columns:
                conn.execute(
                    self.table.update()
                    .where(self.table.c[self._id_column] == entity_id)
                    .values({self._version_column: 1})
                )
        return entity_id

    def update(
        self,
        entity_id: int,
        data: dict[str, Any],
        user: str = "system",
        expected_version: int | None = None,
    ) -> bool:
        """Update a claim, re-checking the gate and syncing evidence links.

        Raises:
            EvidenceGateError: the resulting status is gated but no evidence is
                linked (neither carried in ``data`` nor already on the row).
            OptimisticLockError: ``expected_version`` no longer matches.
        """
        roles = dict(data.get("evidence_roles") or {})
        payload = {k: v for k, v in data.items() if k != "evidence_roles"}
        if "status" in payload or "evidence_ids" in payload or "source_ids" in payload:
            payload = self._normalize(payload)

        with self._engine.connect() as conn:
            current = conn.execute(
                self.table.select().where(self.table.c[self._id_column] == entity_id)
            ).first()
        if current is None:
            return False

        resulting_status = payload.get("status", current.status)
        resulting_evidence = (
            _parse_id_list(payload.get("evidence_ids"))
            if "evidence_ids" in payload
            else _parse_id_list(current.evidence_ids)
        )
        self._require_evidence(resulting_status, resulting_evidence, "claim update")

        payload.setdefault("updated_at", self._get_timestamp())
        ok = super().update(entity_id, payload, user=user, expected_version=expected_version)

        if ok and "evidence_ids" in payload:
            new_links = set(_parse_id_list(payload["evidence_ids"]))
            self._sync_evidence_links(entity_id, new_links, roles, user)
        return ok

    def _sync_evidence_links(
        self,
        claim_id: int,
        evidence_ids: set[int],
        roles: Mapping[Any, str],
        user: str,
    ) -> None:
        """Add any missing ``claim_evidence`` rows (idempotent) + audit row."""
        with self._engine.connect() as conn:
            existing = {
                int(row.evidence_id)
                for row in conn.execute(
                    text("SELECT evidence_id FROM claim_evidence WHERE claim_id = :c"),
                    {"c": claim_id},
                ).all()
            }
        missing = [e for e in sorted(evidence_ids) if e not in existing]
        if not missing:
            return
        with self._engine.begin() as conn:
            self._insert_evidence_links(conn, claim_id, missing, roles)
            self._log_audit(
                conn,
                claim_id,
                "link_evidence",
                json.dumps(sorted(existing), ensure_ascii=False),
                json.dumps(missing, ensure_ascii=False),
                user,
            )

    # -- queries ------------------------------------------------------------
    def get_evidence(self, claim_id: int) -> list[dict[str, Any]]:
        """Return the linked evidence rows (joined with ``foundation_evidence``)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT ce.id AS link_id, ce.claim_id, ce.evidence_id, ce.role, "
                    "ce.created_at AS linked_at, fe.fact_type, fe.fact_key, fe.tier, "
                    "fe.source, fe.confidence "
                    "FROM claim_evidence ce "
                    "JOIN foundation_evidence fe ON fe.id = ce.evidence_id "
                    "WHERE ce.claim_id = :claim_id ORDER BY ce.id"
                ),
                {"claim_id": claim_id},
            ).all()
        return [dict(row._mapping) for row in rows]

    def get_evidence_ids(self, claim_id: int) -> list[int]:
        """Return the linked ``foundation_evidence.id`` values for a claim."""
        return [int(row["evidence_id"]) for row in self.get_evidence(claim_id)]

    def find_by_type(
        self,
        claim_type: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Claims of one type, optionally narrowed to a lifecycle status."""
        filters: dict[str, Any] = {"claim_type": claim_type}
        if status is not None:
            filters["status"] = _status_value(status)
        return self.find(filters, limit=limit, offset=offset, order_by="id")


class HypothesisRepository(BaseRepository):
    """``hypotheses`` — kind-scoped queries for 'pos' / 'morph_relation' rows."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "hypotheses")

    def _normalize(self, data: Mapping[str, Any]) -> dict[str, Any]:
        out = _storage_view(self, data)
        if "status" in out:
            out["status"] = _status_value(out["status"])
        if "evidence_ids" in out:
            out["evidence_ids"] = _dump_id_list(out["evidence_ids"])
        if "extras" in out and not isinstance(out["extras"], str):
            out["extras"] = json.dumps(out["extras"])
        return out

    def create(self, data: dict[str, Any], user: str = "system") -> int:
        """Insert a hypothesis row (contract dicts accepted) + audit row."""
        return super().create(self._normalize(data), user=user)

    def update(
        self,
        entity_id: int,
        data: dict[str, Any],
        user: str = "system",
        expected_version: int | None = None,
    ) -> bool:
        payload = self._normalize(data)
        payload.setdefault("updated_at", self._get_timestamp())
        return super().update(entity_id, payload, user=user, expected_version=expected_version)

    # -- kind-scoped queries ------------------------------------------------
    def find_by_kind(
        self,
        kind: str,
        subject: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Rows of a single ``kind`` ('pos' | 'morph_relation' | …)."""
        filters: dict[str, Any] = {"kind": kind}
        if subject is not None:
            filters["subject"] = subject
        if status is not None:
            filters["status"] = _status_value(status)
        return self.find(filters, limit=limit, offset=offset, order_by="id")

    def find_pos(
        self, word: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """POS hypotheses, optionally for one headword (``subject='word:{word}'``)."""
        subject = f"word:{word}" if word is not None else None
        return self.find_by_kind("pos", subject=subject, limit=limit, offset=offset)

    def find_morph_relations(
        self, surface: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Morphological relations, optionally for one surface form."""
        subject = f"word:{surface}" if surface is not None else None
        return self.find_by_kind("morph_relation", subject=subject, limit=limit, offset=offset)


class KnowledgeVersionRepository(BaseRepository):
    """``knowledge_versions`` release snapshots.

    The optimistic-lock counter is ``row_version``: the ``version`` column is
    the unique TEXT release identifier (``'2026.10.0'``), not a counter.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "knowledge_versions", version_column="row_version")

    def _normalize(self, data: Mapping[str, Any]) -> dict[str, Any]:
        out = _storage_view(self, data)
        if "status" in out:
            out["status"] = _status_value(out["status"])
        for key in ("source_versions", "row_counts", "quality"):
            if key in out and not isinstance(out[key], str):
                out[key] = json.dumps(out[key])
        return out

    def create(self, data: dict[str, Any], user: str = "system") -> int:
        """Insert a version snapshot + audit row (``version`` must be unique)."""
        return super().create(self._normalize(data), user=user)

    def get_by_version(self, version: str) -> dict[str, Any] | None:
        """Fetch one snapshot by its unique release identifier."""
        return self.find_one({"version": version})
