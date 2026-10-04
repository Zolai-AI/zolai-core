"""Human review queue for knowledge claims (Phase 4 §36).

Service over foundation_review_queue with §15 actions:
approve, reject, edit, merge, split, mark_uncertain, add_evidence.
Every action audited.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from zolai.data.repositories.knowledge import ClaimRepository

log = logging.getLogger(__name__)

REVIEW_ACTIONS = {
    "approve",
    "reject",
    "edit",
    "merge",
    "split",
    "mark_uncertain",
    "add_evidence",
}

REVIEW_STATUS = {
    "pending",
    "in_progress",
    "approved",
    "rejected",
    "deferred",
}


class ReviewQueue:
    """Human review queue for knowledge claims."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._claim_repo = None
        self._init_repo(engine)

    def _init_repo(self, engine):
        self._claim_repo = ClaimRepository(engine)

    def _ensure_table(self) -> None:
        """Ensure foundation_review_queue table exists."""
        with self._engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS foundation_review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_type TEXT NOT NULL,  -- 'claim' | 'hypothesis' | 'pattern'
                    item_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    assignee TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT,  -- JSON
                    UNIQUE(item_type, item_id)
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_review_status ON foundation_review_queue(status)
            """))

    def enqueue(
        self,
        item_type: str,
        item_id: int,
        priority: int = 0,
        assignee: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add an item to the review queue (idempotent)."""
        self._ensure_table()
        now = datetime.now(timezone.utc).isoformat()
        meta = json.dumps(metadata or {}, ensure_ascii=False)

        with self._engine.begin() as conn:
            # Upsert
            result = conn.execute(
                text("""
                    INSERT INTO foundation_review_queue (item_type, item_id, status,
                           priority, assignee, created_at, updated_at, metadata)
                    VALUES (:type, :id, 'pending', :priority, :assignee, :created, :updated, :meta)
                    ON CONFLICT(item_type, item_id) DO UPDATE SET
                        status = 'pending',
                        priority = :priority,
                        assignee = :assignee,
                        updated_at = :updated,
                        metadata = :meta
                """),
                {
                    "type": item_type,
                    "id": item_id,
                    "priority": priority,
                    "assignee": assignee,
                    "created": now,
                    "updated": now,
                    "meta": meta,
                },
            )
            return result.lastrowid

    def get_queue(
        self,
        status: str | None = None,
        item_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get paginated review queue items."""
        self._ensure_table()
        filters = []
        params = {"limit": limit, "offset": offset}

        if status:
            filters.append("status = :status")
            params["status"] = status
        if item_type:
            filters.append("item_type = :item_type")
            params["item_type"] = item_type

        where = "WHERE " + " AND ".join(filters) if filters else ""

        with self._engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, item_type, item_id, status, priority, assignee,
                           created_at, updated_at, metadata
                    FROM foundation_review_queue
                    {where}
                    ORDER BY priority DESC, created_at ASC
                    LIMIT :limit OFFSET :offset
                """),
                params,
            ).fetchall()

        result = []
        for row in rows:
            d = dict(row._mapping)
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            result.append(d)
        return result

    def get_item(self, item_type: str, item_id: int) -> dict[str, Any] | None:
        """Get a specific queue item."""
        self._ensure_table()
        with self._engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT id, item_type, item_id, status, priority, assignee,
                           created_at, updated_at, metadata
                    FROM foundation_review_queue
                    WHERE item_type = :type AND item_id = :id
                """),
                {"type": item_type, "id": item_id},
            ).first()
        if row:
            d = dict(row._mapping)
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            return d
        return None

    def process_action(
        self,
        item_type: str,
        item_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        user: str = "system",
    ) -> dict[str, Any]:
        """Process a review action on a queue item."""
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"Invalid action: {action}. Valid: {REVIEW_ACTIONS}")

        self._ensure_table()
        payload = payload or {}
        now = datetime.now(timezone.utc).isoformat()

        with self._engine.begin() as conn:
            # Get current queue item
            row = conn.execute(
                text("SELECT * FROM foundation_review_queue WHERE item_type = :t AND item_id = :id"),
                {"t": item_type, "id": item_id},
            ).first()
            if not row:
                raise ValueError(f"Item not in queue: {item_type}:{item_id}")

            old_status = row.status
            old_meta = json.loads(row.metadata) if row.metadata else {}

            # Process action based on item_type
            result = {"action": action, "item_type": item_type, "item_id": item_id}

            if item_type == "claim":
                result = self._process_claim_action(conn, item_id, action, payload, user, result)
            elif item_type == "hypothesis":
                result = self._process_hypothesis_action(conn, item_id, action, payload, user, result)
            elif item_type == "pattern":
                result = self._process_pattern_action(conn, item_id, action, payload, user, result)
            else:
                raise ValueError(f"Unknown item_type: {item_type}")

            # Update queue item
            new_status = "approved" if action in ("approve", "merge") else \
                         "rejected" if action == "reject" else \
                         "deferred" if action == "mark_uncertain" else "in_progress"

            new_meta = {**old_meta, **payload}
            conn.execute(
                text("""
                    UPDATE foundation_review_queue SET
                        status = :status,
                        updated_at = :updated,
                        metadata = :meta
                    WHERE item_type = :type AND item_id = :id
                """),
                {
                    "status": new_status,
                    "updated": now,
                    "meta": json.dumps(new_meta, ensure_ascii=False),
                    "type": item_type,
                    "id": item_id,
                },
            )

            # Audit log
            conn.execute(
                text("""
                    INSERT INTO data_audit_log (table_name, row_id, field, old_value, new_value, reason, changed_by)
                    VALUES (:table, :row, :field, :old, :new, :reason, :by)
                """),
                {
                    "table": "foundation_review_queue",
                    "row": row.id,
                    "field": "status",
                    "old": old_status,
                    "new": new_status,
                    "reason": f"review_action_{action} by {user}",
                    "by": user,
                },
            )

            result["queue_status"] = new_status
            return result

    def _process_claim_action(
        self,
        conn,
        claim_id: int,
        action: str,
        payload: dict[str, Any],
        user: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Process action on a knowledge claim."""
        claim_repo = ClaimRepository(self._engine)

        if action == "approve":
            # Promote status: CANDIDATE→SUPPORTED→VERIFIED
            current = conn.execute(
                text("SELECT status FROM knowledge_claims WHERE id = :id"),
                {"id": claim_id},
            ).first()
            if not current:
                raise ValueError(f"Claim {claim_id} not found")

            status_map = {
                "CANDIDATE": "SUPPORTED",
                "SUPPORTED": "VERIFIED",
            }
            new_status = status_map.get(current.status)
            if not new_status:
                raise ValueError(f"Cannot approve claim with status {current.status}")

            claim_repo.update(claim_id, {"status": new_status}, user=user)
            result["new_status"] = new_status

        elif action == "reject":
            claim_repo.update(claim_id, {"status": "REJECTED"}, user=user)
            result["new_status"] = "REJECTED"

        elif action == "edit":
            # Allow editing specific fields
            editable = {"claim_type", "subject", "predicate", "object", "notes"}
            updates = {k: v for k, v in payload.items() if k in editable}
            if updates:
                claim_repo.update(claim_id, updates, user=user)
            result["edited_fields"] = list(updates.keys())

        elif action == "add_evidence":
            evidence_ids = payload.get("evidence_ids", [])
            if not isinstance(evidence_ids, list):
                raise ValueError("evidence_ids must be a list")
            # Get current evidence_ids
            current = conn.execute(
                text("SELECT evidence_ids FROM knowledge_claims WHERE id = :id"),
                {"id": claim_id},
            ).first()
            existing = _parse_evidence_ids(current.evidence_ids) if current else []
            new_evidence = list(set(existing) | set(evidence_ids))
            claim_repo.update(claim_id, {"evidence_ids": new_evidence}, user=user)
            result["evidence_ids"] = new_evidence

        elif action == "merge":
            target_id = payload.get("target_claim_id")
            if not target_id:
                raise ValueError("merge requires target_claim_id")
            # TODO: Implement claim merge logic
            result["merged_into"] = target_id

        elif action == "split":
            # TODO: Implement claim split logic
            result["note"] = "split not yet implemented"

        elif action == "mark_uncertain":
            claim_repo.update(claim_id, {"status": "CANDIDATE"}, user=user)
            result["new_status"] = "CANDIDATE"

        return result

    def _process_hypothesis_action(
        self,
        conn,
        hypo_id: int,
        action: str,
        payload: dict[str, Any],
        user: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Process action on a hypothesis."""
        # Similar pattern for hypotheses
        result["note"] = f"hypothesis {action} not fully implemented"
        return result

    def _process_pattern_action(
        self,
        conn,
        pattern_id: int,
        action: str,
        payload: dict[str, Any],
        user: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Process action on a grammar pattern."""
        result["note"] = f"pattern {action} not fully implemented"
        return result


def _parse_evidence_ids(value: Any) -> list[int]:
    import json
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [int(v) for v in value]
