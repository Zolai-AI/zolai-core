"""Quarantined intake for user-authored Zolai learning candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy import text

from ..data.database import DatabaseManager
from ..data.migrations import (
    create_foundation_tables,
    create_user_text_submission_tables,
)
from ..shared.clock import stamp
from ..zvs import validate
from .analysis import FoundationAnalyzer


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _sentences(text_value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?]+\s*", text_value) if part.strip()]


class UserTextSubmissionService:
    """Analyze and quarantine user text without touching canonical tables."""

    def __init__(self, mgr: DatabaseManager, analyzer: FoundationAnalyzer | None = None) -> None:
        self.mgr = mgr
        self.analyzer = analyzer or FoundationAnalyzer()

    def submit(
        self,
        content: str,
        content_type: str,
        *,
        user_id: int | None = None,
        submitted_by: str | None = None,
        auth_source: str | None = None,
    ) -> dict[str, Any]:
        normalized = " ".join(content.split())
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        create_foundation_tables(self.mgr)
        create_user_text_submission_tables(self.mgr)

        with self.mgr.engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT id, validation_status, quarantine_reason "
                    "FROM foundation_user_submissions WHERE content_hash = :hash"
                ),
                {"hash": content_hash},
            ).first()
            if existing:
                return {
                    "id": existing[0],
                    "content_hash": content_hash,
                    "status": "quarantined",
                    "validation_status": existing[1],
                    "quarantine_reason": existing[2],
                    "duplicate": True,
                }

            raw = conn.execute(
                text(
                    "INSERT INTO foundation_raw_corpus "
                    "(source_type, source_path, content_hash, payload, imported_at) "
                    "VALUES (:source_type, :source_path, :hash, :payload, :created)"
                ),
                {
                    "source_type": "user_submission",
                    "source_path": "api:user-text",
                    "hash": content_hash,
                    "payload": json.dumps(
                        {"text": normalized, "content_type": content_type},
                        ensure_ascii=False,
                    ),
                    "created": stamp(),
                },
            )
            raw_id = raw.lastrowid

            try:
                sentences = _sentences(normalized)
                if not sentences:
                    raise ValueError("no analyzable sentences")
                analyses = [self.analyzer.analyze_sentence(sentence) for sentence in sentences]
                zvs_report = validate(normalized, source="user-submission", context="modern")
                zvs_compliant = all(item.zvs_compliant for item in analyses) and zvs_report.is_valid
                sov_valid = all(item.sov_valid for item in analyses)
                if not zvs_compliant:
                    validation_status = "non_zvs"
                    reason = "zvs_validation_failed"
                elif all(item.sov_valid for item in analyses):
                    validation_status = "passed"
                    reason = "awaiting_evidence_review"
                else:
                    validation_status = "mixed" if any(item.sov_valid for item in analyses) else "failed"
                    reason = "sov_validation_failed"
                analysis = {
                    "items": [_jsonable(item) for item in analyses],
                    "zvs_report": zvs_report.to_dict(),
                }
            except Exception:
                analysis = {"items": [], "error": "analysis_failed"}
                zvs_compliant = False
                sov_valid = False
                validation_status = "failed"
                reason = "analysis_failed"

            created = stamp()
            provenance = {
                "source": "user_submission",
                "raw_id": raw_id,
                "content_hash": content_hash,
                "auth_source": auth_source,
            }
            row = conn.execute(
                text(
                    "INSERT INTO foundation_user_submissions "
                    "(raw_id, content_hash, content_type, user_id, submitted_by, auth_source, "
                    "analysis_json, provenance_json, zvs_compliant, sov_valid, "
                    "validation_status, quarantine_reason, created_at, updated_at) "
                    "VALUES (:raw_id, :hash, :content_type, :user_id, :submitted_by, :auth_source, "
                    ":analysis, :provenance, :zvs, :sov, :status, :reason, :created, :created)"
                ),
                {
                    "raw_id": raw_id,
                    "hash": content_hash,
                    "content_type": content_type,
                    "user_id": user_id,
                    "submitted_by": submitted_by,
                    "auth_source": auth_source,
                    "analysis": json.dumps(analysis, ensure_ascii=False),
                    "provenance": json.dumps(provenance, ensure_ascii=False),
                    "zvs": int(zvs_compliant),
                    "sov": int(sov_valid),
                    "status": validation_status,
                    "reason": reason,
                    "created": created,
                },
            )
            return {
                "id": row.lastrowid,
                "content_hash": content_hash,
                "status": "quarantined",
                "validation_status": validation_status,
                "quarantine_reason": reason,
                "duplicate": False,
            }
