"""Regression checker for Phase 5 §36.

Compares key metrics against baseline knowledge version.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


@dataclass
class RegressionReport:
    """Result of regression checks."""
    passed: bool = True
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _get_table_row_counts(engine: Engine, tables: list[str]) -> dict[str, int]:
    """Get row counts for specified tables."""
    counts = {}
    with engine.connect() as conn:
        for table in tables:
            try:
                row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
                counts[table] = row[0] if row else 0
            except Exception as e:
                counts[table] = -1
                log.warning("Failed to count %s: %s", table, e)
    return counts


def _get_baseline_counts(engine: Engine, version_id: int) -> dict[str, int] | None:
    """Get row counts from a knowledge version snapshot."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT row_counts FROM knowledge_versions WHERE id = :id"),
            {"id": version_id},
        ).first()
    if row and row[0]:
        import json
        return json.loads(row[0])
    return None


def _check_zvs_violations(engine: Engine) -> dict[str, int]:
    """Check ZVS violation rates."""
    # Sample check - would be expensive to validate all
    return {"status": "skipped"}


def run_regression_checks(
    engine: Engine,
    baseline_version_id: int | None = None,
    row_count_threshold: float = 0.05,  # 5%
    zvs_threshold: float = 0.0,  # 0% increase
) -> RegressionReport:
    """Run regression checks against baseline version."""
    report = RegressionReport()

    # Canonical tables to check
    canonical_tables = [
        "dictionary", "dictionary_en_zo", "bible_verses", "vocabulary",
        "phrases", "translations", "word_usage", "grammar_patterns",
        "word_collocations", "word_alignments", "training_exercises",
        "syllable_data", "proverbs", "knowledge_claims", "hypotheses",
        "foundation_evidence", "observations", "word_observation_stats",
        "attestation_index",
    ]

    # Get current counts
    current_counts = _get_table_row_counts(engine, canonical_tables)
    report.checks["current_counts"] = current_counts

    # Get baseline
    if baseline_version_id:
        baseline_counts = _get_baseline_counts(engine, baseline_version_id)
    else:
        # Use latest version
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM knowledge_versions ORDER BY id DESC LIMIT 1")
            ).first()
        if row:
            baseline_counts = _get_baseline_counts(engine, row[0])
        else:
            baseline_counts = None

    if baseline_counts:
        report.checks["baseline_counts"] = baseline_counts

        # Compare row counts
        count_diffs = {}
        for table, current in current_counts.items():
            baseline = baseline_counts.get(table, 0)
            if baseline > 0:
                diff_pct = abs(current - baseline) / baseline
                if diff_pct > row_count_threshold:
                    report.passed = False
                    count_diffs[table] = {
                        "baseline": baseline,
                        "current": current,
                        "diff_pct": round(diff_pct * 100, 2),
                        "threshold_pct": round(row_count_threshold * 100, 2),
                    }
        report.checks["count_diffs"] = count_diffs

    # ZVS check (placeholder - would need actual validation)
    report.checks["zvs"] = {"status": "placeholder"}

    # Evidence coverage check
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    COUNT(*) as total_claims,
                    SUM(CASE WHEN evidence_ids != '[]' AND evidence_ids != '' THEN 1 ELSE 0 END) as with_evidence
                FROM knowledge_claims
            """)
        ).first()
        if row:
            total, with_ev = row
            coverage = with_ev / total if total > 0 else 0
            report.checks["evidence_coverage"] = {
                "total_claims": total,
                "with_evidence": with_ev,
                "coverage_pct": round(coverage * 100, 2),
            }
            if coverage < 0.5:  # Expect at least 50% coverage
                report.passed = False

    # Hypothesis status check - no regression to OBSERVED
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, COUNT(*) FROM hypotheses GROUP BY status")
        ).fetchall()
        status_dist = {r[0]: r[1] for r in row}
        report.checks["hypothesis_status_dist"] = status_dist
        # If OBSERVED count increased significantly, might be regression
        # (But OBSERVED is valid for new hypotheses)

    log.info("Regression checks: passed=%s", report.passed)
    return report
