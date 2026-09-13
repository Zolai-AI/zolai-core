"""Provenance service for file tracking and import logs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from ..repositories import AuditRepository, ProvenanceRepository


class ProvenanceService:
    """Service for provenance tracking and import logging."""

    def __init__(self, engine: Engine) -> None:
        self.repo = ProvenanceRepository(engine)
        self.audit = AuditRepository(engine)
        self.engine = engine

    def compute_file_hash(self, filepath: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def count_jsonl_rows(self, filepath: Path) -> int:
        """Count rows in a JSONL file."""
        count = 0
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    def record_file_ingestion(
        self,
        filepath: Path,
        source: str,
        generator_script: str,
        version: str = "1.0",
        status: str = "active",
        user: str = "system",
    ) -> int:
        """Record ingestion of a data file."""
        filename = str(filepath)
        size_bytes = filepath.stat().st_size
        sha256 = self.compute_file_hash(filepath)
        row_count = self.count_jsonl_rows(filepath)

        # Check if already exists
        existing = self.repo.get_by_filename(filename)
        if existing:
            # Update existing record
            self.repo.update_checksum(filename, sha256, user=user)
            self.repo.add_change_log_entry(
                filename,
                {"event": "re-ingestion", "row_count": row_count, "size": size_bytes},
                user,
            )
            return existing["id"]

        return self.repo.record_ingestion(
            filename=filename,
            size_bytes=size_bytes,
            sha256=sha256,
            row_count=row_count,
            source=source,
            generator_script=generator_script,
            version=version,
            status=status,
        )

    def get_file_status(self, filename: str) -> dict[str, Any] | None:
        """Get provenance record for a file."""
        return self.repo.get_by_filename(filename)

    def get_active_files(self) -> list[dict[str, Any]]:
        """Get all active file records."""
        return self.repo.get_active_files()

    def get_files_by_source(self, source: str) -> list[dict[str, Any]]:
        """Get files by source type."""
        return self.repo.get_by_source(source)

    def get_files_by_generator(self, script: str) -> list[dict[str, Any]]:
        """Get files by generator script."""
        return self.repo.get_by_generator(script)

    def mark_deprecated(self, filename: str, user: str = "system") -> bool:
        """Mark a file as deprecated."""
        return self.repo.update_status(filename, "deprecated", user=user)

    def mark_archived(self, filename: str, user: str = "system") -> bool:
        """Mark a file as archived."""
        return self.repo.update_status(filename, "archived", user=user)

    def get_totals(self) -> dict[str, Any]:
        """Get total size and row counts."""
        return {
            "total_size_bytes": self.repo.get_total_size(),
            "total_rows": self.repo.get_total_rows(),
        }

    def get_recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent audit log entries."""
        return self.audit.get_recent(limit=limit)

    def get_table_activity(self) -> dict[str, int]:
        """Get audit activity per table."""
        return self.audit.get_table_activity()

    def get_operation_stats(self) -> dict[str, int]:
        """Get audit operation statistics."""
        return self.audit.get_operation_stats()

    def verify_file_integrity(self, filename: str) -> dict[str, Any]:
        """Verify a file's integrity against stored hash."""
        record = self.repo.get_by_filename(filename)
        if not record:
            return {"verified": False, "error": "File not in provenance"}

        filepath = Path(filename)
        if not filepath.exists():
            return {"verified": False, "error": "File not found on disk"}

        current_hash = self.compute_file_hash(filepath)
        stored_hash = record.get("sha256", "")

        return {
            "verified": current_hash == stored_hash,
            "stored_hash": stored_hash,
            "current_hash": current_hash,
            "filename": filename,
        }
