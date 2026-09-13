"""Provenance and audit repositories for data tracking."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class ProvenanceRepository(BaseRepository):
    """Repository for provenance tracking (provenance table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "provenance", id_column="id")

    def get_by_filename(self, filename: str) -> dict[str, Any] | None:
        """Get provenance record by filename."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(self.table.c.filename == filename)
            ).first()
        return self._row_to_dict(row) if row else None

    def get_by_status(self, status: str) -> list[dict[str, Any]]:
        """Get provenance records by status (active, archived, deprecated)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.status == status)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_source(self, source: str) -> list[dict[str, Any]]:
        """Get provenance records by source type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.source == source)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_generator(self, script: str) -> list[dict[str, Any]]:
        """Get provenance records by generator script."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().where(self.table.c.generator_script == script)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_active_files(self) -> list[dict[str, Any]]:
        """Get all active provenance records."""
        return self.get_by_status("active")

    def get_total_size(self) -> int:
        """Get total size of all tracked files in bytes."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text("SELECT SUM(size_bytes) FROM provenance WHERE status = 'active'")
            ).scalar()
        return result or 0

    def get_total_rows(self) -> int:
        """Get total row count across all tracked files."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text("SELECT SUM(row_count) FROM provenance WHERE status = 'active'")
            ).scalar()
        return result or 0

    def update_checksum(self, filename: str, sha256: str, user: str = "system") -> bool:
        """Update the SHA256 checksum for a file."""
        record = self.get_by_filename(filename)
        if not record:
            return False
        return self.update(
            record["id"], {"sha256": sha256, "updated_at": self._get_timestamp()}, user=user
        )

    def update_status(self, filename: str, status: str, user: str = "system") -> bool:
        """Update the status of a file."""
        record = self.get_by_filename(filename)
        if not record:
            return False
        return self.update(
            record["id"],
            {"status": status, "updated_at": self._get_timestamp()},
            user=user,
        )

    def add_change_log_entry(self, filename: str, change: dict[str, Any], user: str = "system") -> bool:
        """Add an entry to the change_log JSON array."""
        record = self.get_by_filename(filename)
        if not record:
            return False

        try:
            change_log = json.loads(record.get("change_log", "[]"))
        except json.JSONDecodeError:
            change_log = []

        change_log.append({
            "timestamp": self._get_timestamp(),
            "change": change,
            "user": user,
        })

        return self.update(
            record["id"],
            {"change_log": json.dumps(change_log, ensure_ascii=False)},
            user=user,
        )

    def record_ingestion(
        self,
        filename: str,
        size_bytes: int,
        sha256: str,
        row_count: int,
        source: str,
        generator_script: str,
        version: str = "1.0",
        status: str = "active",
    ) -> int:
        """Record a new file ingestion."""
        data = {
            "filename": filename,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "row_count": row_count,
            "source": source,
            "generator_script": generator_script,
            "version": version,
            "status": status,
            "updated_at": self._get_timestamp(),
            "change_log": json.dumps([], ensure_ascii=False),
        }
        return self.create(data)


class AuditRepository(BaseRepository):
    """Repository for audit log queries (data_audit_log table)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "data_audit_log", id_column="id")

    def get_by_table(self, table_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get audit entries for a specific table."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.table_name == table_name)
                .order_by(self.table.c.changed_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_row(self, table_name: str, row_id: int) -> list[dict[str, Any]]:
        """Get audit entries for a specific row."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(
                    (self.table.c.table_name == table_name)
                    & (self.table.c.row_id == row_id)
                )
                .order_by(self.table.c.changed_at.desc())
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_operation(self, operation: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get audit entries by operation type (create, update, delete, etc.)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.field == operation)
                .order_by(self.table.c.changed_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_by_user(self, user: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get audit entries by user/reason (parsed from reason field)."""
        pattern = f"%{user}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.reason.ilike(pattern))
                .order_by(self.table.c.changed_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get most recent audit entries across all tables."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .order_by(self.table.c.changed_at.desc())
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_date_range(
        self, start: str, end: str, table_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Get audit entries within a date range (ISO format)."""
        with self._engine.connect() as conn:
            query = self.table.select().where(
                (self.table.c.changed_at >= start) & (self.table.c.changed_at <= end)
            )
            if table_name:
                query = query.where(self.table.c.table_name == table_name)
            query = query.order_by(self.table.c.changed_at.desc())
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_table_activity(self) -> dict[str, int]:
        """Get audit entry count per table."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name, COUNT(*) as cnt FROM data_audit_log "
                    "GROUP BY table_name ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def get_operation_stats(self) -> dict[str, int]:
        """Get audit entry count per operation type."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT field, COUNT(*) as cnt FROM data_audit_log "
                    "GROUP BY field ORDER BY cnt DESC"
                )
            ).fetchall()
        return {row[0]: row[1] for row in rows}
