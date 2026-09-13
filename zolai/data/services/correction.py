"""Correction service for correction proposals, review, and approval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Table, text
from sqlalchemy.engine import Engine

from ..models import Base


class CorrectionService:
    """Service for managing correction workflow.

    Supports:
    - Proposing corrections
    - Reviewing corrections
    - Approving/rejecting corrections
    - Applying approved corrections
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._corrections_table: Table | None = None

    @property
    def corrections_table(self) -> Table:
        """Get or create corrections tracking table."""
        if self._corrections_table is None:
            Base.metadata.reflect(bind=self.engine)
            if "corrections" in Base.metadata.tables:
                self._corrections_table = Base.metadata.tables["corrections"]
            else:
                # Create the table if it doesn't exist
                from sqlalchemy import Column, Integer, String, Text
                self._corrections_table = Table(
                    "corrections",
                    Base.metadata,
                    Column("id", Integer, primary_key=True, autoincrement=True),
                    Column("table_name", String, nullable=False, index=True),
                    Column("row_id", Integer, nullable=False),
                    Column("field", String, nullable=False),
                    Column("current_value", Text, nullable=True),
                    Column("proposed_value", Text, nullable=False),
                    Column("status", String, nullable=False, default="pending"),  # pending, approved, rejected, applied
                    Column("proposed_by", String, nullable=False),
                    Column("proposed_at", String, nullable=False),
                    Column("reviewed_by", String, nullable=True),
                    Column("reviewed_at", String, nullable=True),
                    Column("review_notes", Text, nullable=True),
                    Column("applied_at", String, nullable=True),
                )
                Base.metadata.create_all(self.engine)
        return self._corrections_table

    def propose_correction(
        self,
        table_name: str,
        row_id: int,
        field: str,
        current_value: str | None,
        proposed_value: str,
        proposed_by: str = "system",
    ) -> int:
        """Propose a correction for a data value."""
        with self.engine.begin() as conn:
            result = conn.execute(
                self.corrections_table.insert(),
                {
                    "table_name": table_name,
                    "row_id": row_id,
                    "field": field,
                    "current_value": current_value,
                    "proposed_value": proposed_value,
                    "status": "pending",
                    "proposed_by": proposed_by,
                    "proposed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return result.inserted_primary_key[0]

    def get_pending_corrections(
        self, table_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get pending corrections for review."""
        with self.engine.connect() as conn:
            query = self.corrections_table.select().where(
                self.corrections_table.c.status == "pending"
            )
            if table_name:
                query = query.where(self.corrections_table.c.table_name == table_name)
            query = query.order_by(self.corrections_table.c.proposed_at.desc()).limit(limit)
            rows = conn.execute(query).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def get_corrections_by_status(
        self, status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get corrections by status."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                self.corrections_table.select()
                .where(self.corrections_table.c.status == status)
                .order_by(self.corrections_table.c.proposed_at.desc())
                .limit(limit)
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def review_correction(
        self,
        correction_id: int,
        reviewer: str,
        approved: bool,
        notes: str = "",
    ) -> bool:
        """Review a correction (approve or reject)."""
        new_status = "approved" if approved else "rejected"
        with self.engine.begin() as conn:
            result = conn.execute(
                self.corrections_table.update()
                .where(self.corrections_table.c.id == correction_id)
                .values(
                    status=new_status,
                    reviewed_by=reviewer,
                    reviewed_at=datetime.now(timezone.utc).isoformat(),
                    review_notes=notes,
                )
            )
        return result.rowcount > 0

    def apply_correction(self, correction_id: int, user: str = "system") -> bool:
        """Apply an approved correction to the target table."""
        with self.engine.begin() as conn:
            # Get the correction
            corr = conn.execute(
                self.corrections_table.select().where(
                    self.corrections_table.c.id == correction_id
                )
            ).first()

            if not corr:
                return False

            if corr.status != "approved":
                return False

            # Apply to target table
            target_table = Table(
                corr.table_name, Base.metadata, autoload_with=self.engine
            )

            conn.execute(
                target_table.update()
                .where(target_table.c.id == corr.row_id)
                .values({corr.field: corr.proposed_value})
            )

            # Log to audit
            audit_table = Table("data_audit_log", Base.metadata, autoload_with=self.engine)
            conn.execute(
                audit_table.insert(),
                {
                    "table_name": corr.table_name,
                    "row_id": corr.row_id,
                    "field": corr.field,
                    "old_value": corr.current_value,
                    "new_value": corr.proposed_value,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "reason": f"correction_applied:{correction_id}:{user}",
                },
            )

            # Mark correction as applied
            conn.execute(
                self.corrections_table.update()
                .where(self.corrections_table.c.id == correction_id)
                .values(
                    status="applied",
                    applied_at=datetime.now(timezone.utc).isoformat(),
                )
            )

        return True

    def bulk_propose_from_dict(
        self,
        table_name: str,
        corrections: list[dict[str, Any]],
        proposed_by: str = "system",
    ) -> int:
        """Bulk propose corrections from a list of dicts.

        Each dict should have: row_id, field, current_value, proposed_value
        """
        count = 0
        for corr in corrections:
            self.propose_correction(
                table_name=table_name,
                row_id=corr["row_id"],
                field=corr["field"],
                current_value=corr.get("current_value"),
                proposed_value=corr["proposed_value"],
                proposed_by=proposed_by,
            )
            count += 1
        return count

    def get_correction_stats(self) -> dict[str, int]:
        """Get correction workflow statistics."""
        with self.engine.connect() as conn:
            pending = conn.execute(
                text("SELECT COUNT(*) FROM corrections WHERE status = 'pending'")
            ).scalar()
            approved = conn.execute(
                text("SELECT COUNT(*) FROM corrections WHERE status = 'approved'")
            ).scalar()
            rejected = conn.execute(
                text("SELECT COUNT(*) FROM corrections WHERE status = 'rejected'")
            ).scalar()
            applied = conn.execute(
                text("SELECT COUNT(*) FROM corrections WHERE status = 'applied'")
            ).scalar()

        return {
            "pending": pending or 0,
            "approved": approved or 0,
            "rejected": rejected or 0,
            "applied": applied or 0,
            "total": (pending or 0) + (approved or 0) + (rejected or 0) + (applied or 0),
        }

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {col: getattr(row, col, None) for col in row._mapping.keys()}
