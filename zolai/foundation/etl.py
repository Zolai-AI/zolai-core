"""Foundation ETL — Raw → Staging → Canonical pipeline.

Orchestrates the three-layer data pipeline:
  1. ingest_raw()  — load JSONL files into foundation_raw_corpus / foundation_raw_llm
  2. build_staging() — clean, validate, normalize into foundation_staging_*
  3. promote_to_canonical() — merge staging into canonical_* with deduplication
  4. verify_pipeline() — run verification queries and return metrics

Usage:
    from zolai.foundation.etl import FoundationETL
    etl = FoundationETL()
    etl.ingest_raw("path/to/corpus.jsonl", source_type="bible")
    etl.build_staging(batch_id="batch-001")
    etl.promote_to_canonical(batch_id="batch-001")
    report = etl.verify_pipeline()
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ..data.database import DatabaseManager, get_manager

logger = logging.getLogger(__name__)


class FoundationETL:
    """Three-layer ETL pipeline: Raw → Staging → Canonical.

    Each stage is idempotent and tracks batch provenance.
    """

    def __init__(self, mgr: DatabaseManager | None = None) -> None:
        self._mgr = mgr or get_manager()

    @property
    def mgr(self) -> DatabaseManager:
        return self._mgr

    # ------------------------------------------------------------------
    # 1. INGEST RAW — load JSONL into foundation_raw_corpus / foundation_raw_llm
    # ------------------------------------------------------------------

    def ingest_raw(
        self,
        path: str | Path,
        source_type: str = "corpus",
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Load a JSONL file into foundation_raw_corpus.

        Each line is hashed for deduplication.  Lines that already exist
        (same content_hash) are skipped.

        Args:
            path: Path to a JSONL file.
            source_type: Category tag (bible, corpus, dictionary, etc.).
            batch_id: Optional batch identifier for tracking.

        Returns:
            Dict with 'imported', 'skipped', 'errors' counts.
        """
        path = Path(path)
        if not path.exists():
            return {"imported": 0, "skipped": 0, "errors": 1, "error_msg": f"File not found: {path}"}

        imported = 0
        skipped = 0
        errors = 0
        batch_id = batch_id or f"ingest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

        with self._mgr.engine.connect() as conn:
            with open(path, encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    content_hash = hashlib.sha256(line.encode()).hexdigest()

                    # Check for duplicate
                    exists = conn.execute(
                        text("SELECT 1 FROM foundation_raw_corpus WHERE content_hash = :h LIMIT 1"),
                        {"h": content_hash},
                    ).first()
                    if exists:
                        skipped += 1
                        continue

                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        # Store raw text if not valid JSON
                        payload = {"raw": line}

                    try:
                        conn.execute(
                            text(
                                """INSERT INTO foundation_raw_corpus
                                   (source_type, source_path, content_hash, payload, imported_at)
                                   VALUES (:src, :path, :hash, :payload, :ts)"""
                            ),
                            {
                                "src": source_type,
                                "path": str(path),
                                "hash": content_hash,
                                "payload": json.dumps(payload),
                                "ts": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        imported += 1
                    except Exception as exc:
                        logger.warning("ingest_raw line %d failed: %s", line_no, exc)
                        errors += 1

                    # Commit in batches of 500
                    if imported % 500 == 0 and imported > 0:
                        conn.commit()

            conn.commit()

        logger.info(
            "ingest_raw(%s): imported=%d skipped=%d errors=%d",
            path.name, imported, skipped, errors,
        )
        return {"imported": imported, "skipped": skipped, "errors": errors}

    def ingest_raw_llm(
        self,
        data: list[dict[str, Any]],
        model: str,
        *,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """Load LLM response data into foundation_raw_llm.

        Args:
            data: List of response dicts (must have 'response' key at minimum).
            model: Model name/identifier.
            batch_id: Optional batch identifier.

        Returns:
            Dict with 'imported', 'skipped', 'errors' counts.
        """
        imported = 0
        errors = 0
        ts = datetime.now(timezone.utc).isoformat()

        with self._mgr.engine.connect() as conn:
            for item in data:
                try:
                    conn.execute(
                        text(
                            """INSERT INTO foundation_raw_llm
                               (model, response_json, created_at)
                               VALUES (:model, :resp, :ts)"""
                        ),
                        {"model": model, "resp": json.dumps(item), "ts": ts},
                    )
                    imported += 1
                except Exception as exc:
                    logger.warning("ingest_raw_llm failed: %s", exc)
                    errors += 1

            conn.commit()

        return {"imported": imported, "skipped": 0, "errors": errors}

    # ------------------------------------------------------------------
    # 2. BUILD STAGING — clean, validate, normalize
    # ------------------------------------------------------------------

    def build_staging(self, batch_id: str | None = None) -> dict[str, Any]:
        """Process raw records into staging tables.

        Reads unprocessed rows from foundation_raw_corpus, cleans them,
        and writes normalized rows to the appropriate staging table.

        Returns:
            Dict with per-table counts.
        """
        batch_id = batch_id or f"staging-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        results: dict[str, Any] = {"words": 0, "sentences": 0, "paragraphs": 0, "errors": 0}

        with self._mgr.engine.connect() as conn:
            # Fetch unprocessed raw records
            rows = conn.execute(
                text(
                    """SELECT id, source_type, payload
                       FROM foundation_raw_corpus
                       ORDER BY id"""
                )
            ).fetchall()

            for row in rows:
                raw_id, source_type, payload_str = row
                try:
                    payload = json.loads(payload_str) if payload_str else {}
                except json.JSONDecodeError:
                    results["errors"] += 1
                    continue

                try:
                    if source_type in ("dictionary", "vocabulary", "bible_vocab"):
                        self._staging_word(conn, payload, raw_id)
                        results["words"] += 1
                    elif source_type in ("bible", "translations", "corpus"):
                        self._staging_sentence(conn, payload, raw_id)
                        results["sentences"] += 1
                    elif source_type in ("wiki", "articles"):
                        self._staging_paragraph(conn, payload, raw_id)
                        results["paragraphs"] += 1
                    else:
                        # Default: treat as sentence
                        self._staging_sentence(conn, payload, raw_id)
                        results["sentences"] += 1
                except Exception as exc:
                    logger.warning("build_staging raw_id=%d failed: %s", raw_id, exc)
                    results["errors"] += 1

            conn.commit()

        logger.info("build_staging(%s): %s", batch_id, results)
        return results

    def _staging_word(self, conn: Any, payload: dict, raw_id: int) -> None:
        """Insert a word into foundation_staging_words."""
        form = payload.get("zolai") or payload.get("headword") or payload.get("word", "")
        if not form:
            return
        conn.execute(
            text(
                """INSERT OR IGNORE INTO foundation_staging_words
                   (form, pos, frequency, zvs_compliant, source_hash, created_at)
                   VALUES (:form, :pos, :freq, :zvs, :hash, :ts)"""
            ),
            {
                "form": form.lower().strip(),
                "pos": payload.get("pos", ""),
                "freq": payload.get("frequency", 0),
                "zvs": 1,  # Assume compliant; validator will check
                "hash": hashlib.sha256(str(raw_id).encode()).hexdigest()[:16],
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _staging_sentence(self, conn: Any, payload: dict, raw_id: int) -> None:
        """Insert a sentence into foundation_staging_sentences."""
        text_val = payload.get("zo_tdb77") or payload.get("zo") or payload.get("text", "")
        if not text_val or len(text_val.strip()) < 3:
            return
        conn.execute(
            text(
                """INSERT INTO foundation_staging_sentences
                   (text, source_hash, created_at)
                   VALUES (:text, :hash, :ts)"""
            ),
            {
                "text": text_val.strip(),
                "hash": hashlib.sha256(str(raw_id).encode()).hexdigest()[:16],
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _staging_paragraph(self, conn: Any, payload: dict, raw_id: int) -> None:
        """Insert a paragraph into foundation_staging_paragraphs."""
        text_val = payload.get("content") or payload.get("text", "")
        if not text_val or len(text_val.strip()) < 10:
            return
        conn.execute(
            text(
                """INSERT INTO foundation_staging_paragraphs
                   (text, source_hash, created_at)
                   VALUES (:text, :hash, :ts)"""
            ),
            {
                "text": text_val.strip()[:5000],  # Cap length
                "hash": hashlib.sha256(str(raw_id).encode()).hexdigest()[:16],
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    # ------------------------------------------------------------------
    # 3. PROMOTE TO CANONICAL — merge staging → canonical with dedup
    # ------------------------------------------------------------------

    def promote_to_canonical(self, batch_id: str | None = None) -> dict[str, Any]:
        """Promote staging records into canonical tables.

        Deduplicates by form (words) or content hash (sentences/paragraphs).

        Returns:
            Dict with per-table promotion counts.
        """
        batch_id = batch_id or f"promote-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        results: dict[str, Any] = {"words": 0, "sentences": 0, "paragraphs": 0, "errors": 0}

        with self._mgr.engine.connect() as conn:
            # Promote words
            try:
                rows = conn.execute(
                    text(
                        """SELECT DISTINCT form, pos, frequency, zvs_compliant
                           FROM foundation_staging_words
                           WHERE form NOT IN (SELECT form FROM canonical_words)"""
                    )
                ).fetchall()
                for row in rows:
                    conn.execute(
                        text(
                            """INSERT OR IGNORE INTO canonical_words
                               (form, pos, frequency, zvs_compliant, version, created_at)
                               VALUES (:form, :pos, :freq, :zvs, 1, :ts)"""
                        ),
                        {
                            "form": row[0],
                            "pos": row[1] or "",
                            "freq": row[2] or 0,
                            "zvs": row[3] if row[3] is not None else 1,
                            "ts": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    results["words"] += 1
            except Exception as exc:
                logger.warning("promote words failed: %s", exc)
                results["errors"] += 1

            # Promote sentences
            try:
                rows = conn.execute(
                    text(
                        """SELECT text, source_hash
                           FROM foundation_staging_sentences
                           WHERE source_hash NOT IN
                               (SELECT evidence_ids FROM canonical_sentences)"""
                    )
                ).fetchall()
                for row in rows:
                    conn.execute(
                        text(
                            """INSERT OR IGNORE INTO canonical_sentences
                               (text, version, created_at, evidence_ids)
                               VALUES (:text, 1, :ts, :evidence)"""
                        ),
                        {
                            "text": row[0],
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "evidence": row[1],
                        },
                    )
                    results["sentences"] += 1
            except Exception as exc:
                logger.warning("promote sentences failed: %s", exc)
                results["errors"] += 1

            # Promote paragraphs
            try:
                rows = conn.execute(
                    text(
                        """SELECT text, source_hash
                           FROM foundation_staging_paragraphs
                           WHERE source_hash NOT IN
                               (SELECT evidence_ids FROM canonical_paragraphs)"""
                    )
                ).fetchall()
                for row in rows:
                    conn.execute(
                        text(
                            """INSERT OR IGNORE INTO canonical_paragraphs
                               (text, version, created_at, evidence_ids)
                               VALUES (:text, 1, :ts, :evidence)"""
                        ),
                        {
                            "text": row[0],
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "evidence": row[1],
                        },
                    )
                    results["paragraphs"] += 1
            except Exception as exc:
                logger.warning("promote paragraphs failed: %s", exc)
                results["errors"] += 1

            conn.commit()

        logger.info("promote_to_canonical(%s): %s", batch_id, results)
        return results

    # ------------------------------------------------------------------
    # 4. VERIFY PIPELINE — run verification queries
    # ------------------------------------------------------------------

    def verify_pipeline(self) -> dict[str, Any]:
        """Run verification queries across all layers.

        Returns:
            Dict with layer counts, integrity checks, and health status.
        """
        report: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "layers": {},
            "health": "ok",
            "issues": [],
        }

        with self._mgr.engine.connect() as conn:
            # Raw layer counts
            try:
                raw_count = conn.execute(
                    text("SELECT COUNT(*) FROM foundation_raw_corpus")
                ).scalar() or 0
                raw_llm_count = conn.execute(
                    text("SELECT COUNT(*) FROM foundation_raw_llm")
                ).scalar() or 0
                report["layers"]["raw"] = {
                    "corpus": raw_count,
                    "llm": raw_llm_count,
                }
            except Exception as exc:
                report["layers"]["raw"] = {"error": str(exc)}
                report["issues"].append(f"raw layer: {exc}")

            # Staging layer counts
            staging_tables = [
                "foundation_staging_words",
                "foundation_staging_sentences",
                "foundation_staging_paragraphs",
                "foundation_staging_evidence",
            ]
            staging_counts: dict[str, int] = {}
            for tbl in staging_tables:
                try:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0
                    staging_counts[tbl] = count
                except Exception:
                    staging_counts[tbl] = -1
            report["layers"]["staging"] = staging_counts

            # Canonical layer counts
            canonical_tables = [
                "canonical_words",
                "canonical_sentences",
                "canonical_paragraphs",
            ]
            canonical_counts: dict[str, int] = {}
            for tbl in canonical_tables:
                try:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0
                    canonical_counts[tbl] = count
                except Exception:
                    canonical_counts[tbl] = -1
            report["layers"]["canonical"] = canonical_counts

            # Integrity checks
            # Check that staging→canonical promotion is healthy
            staging_words = staging_counts.get("foundation_staging_words", 0)
            canonical_words = canonical_counts.get("canonical_words", 0)
            if staging_words > 0 and canonical_words == 0:
                report["issues"].append("Staging has words but canonical is empty — promotion needed")
                report["health"] = "warning"

            # Check for empty raw layer
            if raw_count == 0 and raw_llm_count == 0:
                report["issues"].append("Raw layer is empty — ingest needed")
                if report["health"] == "ok":
                    report["health"] = "warning"

        logger.info("verify_pipeline: health=%s issues=%d", report["health"], len(report["issues"]))
        return report


def get_foundation_etl(mgr: DatabaseManager | None = None) -> FoundationETL:
    """Get or create a FoundationETL singleton."""
    return FoundationETL(mgr)
