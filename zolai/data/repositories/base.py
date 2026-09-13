"""Base repository with common CRUD operations, transactions, and audit support."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from sqlalchemy import Table, func, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine

T = TypeVar("T")


class OptimisticLockError(Exception):
    """Raised when optimistic locking detects a concurrent modification."""

    pass


class BaseRepository:
    """Base repository providing common CRUD with transactions and audit logging.

    Features:
    - Common CRUD operations with audit trail
    - Optimistic locking via version column
    - Transaction management with context manager
    - Soft delete support
    - Version history and restoration
    """

    def __init__(
        self,
        engine: Engine,
        table_name: str,
        id_column: str = "id",
        version_column: str = "version",
        audit_table: str = "data_audit_log",
    ) -> None:
        self._engine = engine
        self._table_name = table_name
        self._id_column = id_column
        self._version_column = version_column
        self._audit_table = audit_table
        self._table: Table | None = None
        self._audit_table_obj: Table | None = None
        self._metadata = None

    @property
    def table(self) -> Table:
        """Get the SQLAlchemy Table object (lazy-loaded)."""
        if self._table is None:
            from ..models import Base

            Base.metadata.reflect(bind=self._engine)
            self._table = Table(self._table_name, Base.metadata, autoload_with=self._engine)
        return self._table

    @property
    def audit_table_obj(self) -> Table:
        """Get the audit log table."""
        if self._audit_table_obj is None:
            from ..models import Base

            Base.metadata.reflect(bind=self._engine)
            self._audit_table_obj = Table(
                self._audit_table, Base.metadata, autoload_with=self._engine
            )
        return self._audit_table_obj

    # -------------------------------------------------------------------------
    # Transaction Management
    # -------------------------------------------------------------------------
    @contextmanager
    def transaction(self):
        """Context manager for database transactions.

        Usage:
            with repo.transaction() as conn:
                conn.execute(...)
                conn.execute(...)  # both in same transaction
        """
        with self._engine.begin() as conn:
            yield conn

    def execute_in_transaction(self, fn: Callable[[Any], T]) -> T:
        """Execute a function within a transaction.

        Args:
            fn: Function that receives a connection and returns a value.

        Returns:
            The return value of fn.
        """
        with self._engine.begin() as conn:
            return fn(conn)

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------
    def get_by_id(self, entity_id: int) -> dict[str, Any] | None:
        """Find a record by primary key."""
        with self._engine.connect() as conn:
            row = conn.execute(
                self.table.select().where(self.table.c[self._id_column] == entity_id)
            ).first()
        if row:
            return self._row_to_dict(row)
        return None

    def find(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Paginated search with optional filters.

        Args:
            filters: Dictionary of column -> value for WHERE clauses
            limit: Maximum rows to return
            offset: Rows to skip
            order_by: Column name to order by (prefix with '-' for DESC)

        Returns:
            List of matching records as dicts.
        """
        query = self.table.select()

        if filters:
            for col_name, value in filters.items():
                if value is not None:
                    query = query.where(self.table.c[col_name] == value)

        if order_by:
            if order_by.startswith("-"):
                query = query.order_by(self.table.c[order_by[1:]].desc())
            else:
                query = query.order_by(self.table.c[order_by])

        query = query.limit(limit).offset(offset)

        with self._engine.connect() as conn:
            rows = conn.execute(query).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def find_one(self, filters: dict[str, Any]) -> dict[str, Any] | None:
        """Find a single record matching filters."""
        results = self.find(filters, limit=1)
        return results[0] if results else None

    def count(self, filters: dict[str, Any] | None = None) -> int:
        """Count records matching filters."""
        from sqlalchemy import select

        query = select(func.count()).select_from(self.table)

        if filters:
            for col_name, value in filters.items():
                if value is not None:
                    query = query.where(self.table.c[col_name] == value)

        with self._engine.connect() as conn:
            return conn.execute(query).scalar_one()

    def create(self, data: dict[str, Any], user: str = "system") -> int:
        """Insert a new record with audit logging.

        Args:
            data: Column values for the new record
            user: User/process identifier for audit log

        Returns:
            The ID of the newly created record.
        """
        with self._engine.begin() as conn:
            # Insert the record
            result = conn.execute(self.table.insert(), data)
            entity_id = result.inserted_primary_key[0]

            # Log to audit
            self._log_audit(
                conn,
                entity_id,
                "create",
                None,
                json.dumps(data, ensure_ascii=False),
                user,
            )

            # Update version if column exists
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
        """Update a record with optimistic locking and audit logging.

        Args:
            entity_id: Primary key of record to update
            data: New column values
            user: User/process identifier for audit log
            expected_version: If provided, enforces optimistic locking

        Returns:
            True if updated, False if not found.

        Raises:
            OptimisticLockError: If version mismatch (concurrent modification).
        """
        with self._engine.begin() as conn:
            # Check if record exists and get current version
            current = conn.execute(
                self.table.select().where(self.table.c[self._id_column] == entity_id)
            ).first()

            if not current:
                return False

            current_version = getattr(current, self._version_column, 1)

            # Optimistic locking check
            if expected_version is not None and current_version != expected_version:
                raise OptimisticLockError(
                    f"Version mismatch: expected {expected_version}, "
                    f"found {current_version}. Record was modified by another process."
                )

            # Get old values for audit
            old_data = self._row_to_dict(current)

            # Perform update
            update_data = {**data}
            if self._version_column in self.table.columns:
                update_data[self._version_column] = current_version + 1

            conn.execute(
                self.table.update()
                .where(self.table.c[self._id_column] == entity_id)
                .values(update_data)
            )

            # Log to audit
            self._log_audit(
                conn,
                entity_id,
                "update",
                json.dumps(old_data, ensure_ascii=False),
                json.dumps(update_data, ensure_ascii=False),
                user,
            )

        return True

    def soft_delete(
        self, entity_id: int, deleted_by: str = "system", reason: str = ""
    ) -> bool:
        """Mark a record as deleted (soft delete) with audit trail.

        Args:
            entity_id: Primary key of record to delete
            deleted_by: User/process performing deletion
            reason: Reason for deletion

        Returns:
            True if deleted, False if not found.
        """
        # Check if table supports soft delete
        has_deleted = "is_deleted" in self.table.columns

        with self._engine.begin() as conn:
            current = conn.execute(
                self.table.select().where(self.table.c[self._id_column] == entity_id)
            ).first()

            if not current:
                return False

            old_data = self._row_to_dict(current)

            if has_deleted:
                # Soft delete
                update_data = {
                    "is_deleted": 1,
                    "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "deleted_by": deleted_by,
                }
                if "delete_reason" in self.table.columns:
                    update_data["delete_reason"] = reason

                conn.execute(
                    self.table.update()
                    .where(self.table.c[self._id_column] == entity_id)
                    .values(update_data)
                )
            else:
                # Hard delete (fallback)
                conn.execute(
                    self.table.delete().where(self.table.c[self._id_column] == entity_id)
                )

            # Log to audit
            self._log_audit(
                conn,
                entity_id,
                "soft_delete" if has_deleted else "delete",
                json.dumps(old_data, ensure_ascii=False),
                json.dumps({"deleted_by": deleted_by, "reason": reason}, ensure_ascii=False),
                deleted_by,
            )

        return True

    def restore(self, entity_id: int, user: str = "system") -> bool:
        """Restore a soft-deleted record."""
        if "is_deleted" not in self.table.columns:
            return False

        with self._engine.begin() as conn:
            current = conn.execute(
                self.table.select().where(self.table.c[self._id_column] == entity_id)
            ).first()

            if not current:
                return False

            if getattr(current, "is_deleted", 0) == 0:
                return False  # Not deleted

            old_data = self._row_to_dict(current)

            conn.execute(
                self.table.update()
                .where(self.table.c[self._id_column] == entity_id)
                .values(
                    {
                        "is_deleted": 0,
                        "deleted_at": None,
                        "deleted_by": None,
                    }
                )
            )

            self._log_audit(
                conn,
                entity_id,
                "restore",
                json.dumps(old_data, ensure_ascii=False),
                json.dumps({"restored_by": user}, ensure_ascii=False),
                user,
            )

        return True

    # -------------------------------------------------------------------------
    # Version History
    # -------------------------------------------------------------------------
    def get_versions(self, entity_id: int) -> list[dict[str, Any]]:
        """Get version history for a record from audit log."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.audit_table_obj.select()
                .where(
                    (self.audit_table_obj.c.table_name == self._table_name)
                    & (self.audit_table_obj.c.row_id == entity_id)
                )
                .order_by(self.audit_table_obj.c.changed_at.desc())
            ).fetchall()

        return [self._audit_row_to_dict(row) for row in rows]

    def restore_version(self, entity_id: int, version_timestamp: str, user: str = "system") -> bool:
        """Restore a record to a previous version.

        Args:
            entity_id: Primary key of record
            version_timestamp: ISO timestamp of the version to restore to
            user: User performing restoration

        Returns:
            True if restored, False if version not found.
        """
        # Find the audit entry with the target version
        with self._engine.connect() as conn:
            audit_row = conn.execute(
                self.audit_table_obj.select()
                .where(
                    (self.audit_table_obj.c.table_name == self._table_name)
                    & (self.audit_table_obj.c.row_id == entity_id)
                    & (self.audit_table_obj.c.changed_at <= version_timestamp)
                )
                .order_by(self.audit_table_obj.c.changed_at.desc())
                .limit(1)
            ).first()

            if not audit_row:
                return False

            # Get the new_value from that audit entry
            new_value = getattr(audit_row, "new_value", None)
            if not new_value:
                return False

            try:
                data = json.loads(new_value)
            except json.JSONDecodeError:
                return False

            # Apply the restored data
            return self.update(entity_id, data, user=user)

    # -------------------------------------------------------------------------
    # Bulk Operations
    # -------------------------------------------------------------------------
    def bulk_create(self, records: list[dict[str, Any]], user: str = "system") -> list[int]:
        """Insert multiple records in a single transaction with audit logging.

        Args:
            records: List of record data dicts
            user: User/process identifier for audit log

        Returns:
            List of created entity IDs.
        """
        if not records:
            return []

        ids: list[int] = []
        with self._engine.begin() as conn:
            for record in records:
                result = conn.execute(self.table.insert(), record)
                entity_id = result.inserted_primary_key[0]
                ids.append(entity_id)

                self._log_audit(
                    conn,
                    entity_id,
                    "bulk_create",
                    None,
                    json.dumps(record, ensure_ascii=False),
                    user,
                )

        return ids

    def bulk_update(
        self, updates: list[dict[str, Any]], user: str = "system"
    ) -> int:
        """Update multiple records in a single transaction.

        Each update dict must contain the id_column key.

        Args:
            updates: List of {id, field1: value1, ...} dicts
            user: User/process identifier for audit log

        Returns:
            Number of records updated.
        """
        if not updates:
            return 0

        count = 0
        with self._engine.begin() as conn:
            for update in updates:
                entity_id = update.pop(self._id_column, None)
                if entity_id is None:
                    continue

                current = conn.execute(
                    self.table.select().where(self.table.c[self._id_column] == entity_id)
                ).first()

                if not current:
                    continue

                old_data = self._row_to_dict(current)

                update_data = {**update}
                if self._version_column in self.table.columns:
                    current_version = getattr(current, self._version_column, 1)
                    update_data[self._version_column] = current_version + 1

                conn.execute(
                    self.table.update()
                    .where(self.table.c[self._id_column] == entity_id)
                    .values(update_data)
                )

                self._log_audit(
                    conn,
                    entity_id,
                    "bulk_update",
                    json.dumps(old_data, ensure_ascii=False),
                    json.dumps(update_data, ensure_ascii=False),
                    user,
                )
                count += 1

        return count

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert SQLAlchemy Row to dict."""
        return {col: getattr(row, col, None) for col in row._mapping.keys()}

    def _audit_row_to_dict(self, row: Any) -> dict[str, Any]:
        """Convert audit log Row to dict."""
        return {col: getattr(row, col, None) for col in row._mapping.keys()}

    def _log_audit(
        self,
        conn,
        row_id: int,
        operation: str,
        old_value: str | None,
        new_value: str | None,
        user: str,
    ) -> None:
        """Log a change to the audit table."""
        conn.execute(
            self.audit_table_obj.insert(),
            {
                "table_name": self._table_name,
                "row_id": row_id,
                "field": operation,
                "old_value": old_value,
                "new_value": new_value,
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "reason": f"repo:{operation}:{user}",
            },
        )

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------
    def exists(self, entity_id: int) -> bool:
        """Check if a record exists."""
        with self._engine.connect() as conn:
            result = conn.execute(
                text(
                    f"SELECT 1 FROM {self._table_name} WHERE {self._id_column} = :id LIMIT 1"
                ),
                {"id": entity_id},
            ).first()
        return result is not None

    def get_column_values(self, column: str, limit: int = 1000) -> list[Any]:
        """Get distinct values for a column (useful for enums/lookups)."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT DISTINCT {column} FROM {self._table_name} "
                    f"WHERE {column} IS NOT NULL LIMIT :limit"
                ),
                {"limit": limit},
            ).fetchall()
        return [row[0] for row in rows]

    def random_row(self) -> dict[str, Any] | None:
        """Return an arbitrary row (SELECT ... ORDER BY RANDOM() LIMIT 1)."""
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT * FROM {self._table_name} ORDER BY RANDOM() LIMIT 1"
                )
            ).first()
        return dict(row._mapping) if row else None

    def all_records(self, columns: list[str] | None = None) -> list[dict[str, Any]]:
        """Return all rows in the table as dicts (optionally limited columns).

        Used by corpus/validator modules that previously iterated a JSONL file.
        """
        if columns:
            query = self.table.select().with_only_columns(
                *[self.table.c[c] for c in columns]
            )
        else:
            query = self.table.select()
        with self._engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def table_info(self) -> dict[str, Any]:
        """Get table schema information."""
        inspector = sa_inspect(self._engine)
        columns = inspector.get_columns(self._table_name)
        indexes = inspector.get_indexes(self._table_name)
        foreign_keys = inspector.get_foreign_keys(self._table_name)

        return {
            "table_name": self._table_name,
            "columns": [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c["nullable"],
                    "default": c.get("default"),
                }
                for c in columns
            ],
            "indexes": indexes,
            "foreign_keys": foreign_keys,
        }
