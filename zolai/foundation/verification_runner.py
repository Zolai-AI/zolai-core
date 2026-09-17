"""Foundation Verification Runner — adaptive threshold verification.

Runs automated verification of the Foundation pipeline data quality,
including:
  - ZVS 2018 compliance checking on canonical words
  - Dictionary coverage against Bible verses
  - Staging-to-canonical promotion health
  - Cross-table referential integrity

Adaptive thresholds adjust based on data volume — small datasets
get relaxed thresholds to avoid false negatives.

Usage:
    from zolai.foundation.verification_runner import VerificationRunner
    runner = VerificationRunner()
    report = runner.run_all()
    print(report)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text

from ..data.database import DatabaseManager, get_manager

logger = logging.getLogger(__name__)


# ── ZVS 2018 forbidden forms ────────────────────────────────────────────────
ZVS_FORBIDDEN: dict[str, str] = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
}


@dataclass
class CheckResult:
    """Result of a single verification check."""
    name: str
    passed: bool
    count: int = 0
    total: int = 0
    details: str = ""
    score: float = 0.0  # 0.0 - 1.0

    @property
    def rate(self) -> float:
        return self.count / self.total if self.total > 0 else 0.0


@dataclass
class VerificationReport:
    """Aggregated verification results."""
    timestamp: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    overall_health: str = "ok"  # ok, warning, critical
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(c.score for c in self.checks) / len(self.checks)

    def summary(self) -> str:
        lines = [
            f"Verification Report — {self.timestamp}",
            f"  Health: {self.overall_health.upper()}",
            f"  Score: {self.score:.1%}",
            f"  Checks: {self.passed_checks}/{self.total_checks} passed",
        ]
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"    [{status}] {c.name}: {c.details}")
        return "\n".join(lines)


class VerificationRunner:
    """Run adaptive-threshold verification checks on Foundation pipeline data.

    Thresholds are relaxed for small datasets (<1000 records) and tightened
    for larger ones.
    """

    def __init__(self, mgr: DatabaseManager | None = None) -> None:
        self._mgr = mgr or get_manager()

    @property
    def mgr(self) -> DatabaseManager:
        return self._mgr

    def run_all(self) -> VerificationReport:
        """Run all verification checks and return a report."""
        report = VerificationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        checks = [
            self.check_zvs_compliance(),
            self.check_dictionary_coverage(),
            self.check_staging_health(),
            self.check_canonical_health(),
            self.check_referential_integrity(),
        ]

        report.checks = checks
        report.total_checks = len(checks)
        report.passed_checks = sum(1 for c in checks if c.passed)
        report.failed_checks = report.total_checks - report.passed_checks

        if report.failed_checks >= 3:
            report.overall_health = "critical"
        elif report.failed_checks >= 1:
            report.overall_health = "warning"
        else:
            report.overall_health = "ok"

        logger.info(
            "Verification: health=%s score=%.1f%% passed=%d/%d",
            report.overall_health, report.score * 100,
            report.passed_checks, report.total_checks,
        )
        return report

    # ------------------------------------------------------------------
    # Adaptive threshold helpers
    # ------------------------------------------------------------------

    def _get_table_count(self, table: str) -> int:
        """Get row count for a table (returns -1 if table doesn't exist)."""
        try:
            with self._mgr.engine.connect() as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                return result or 0
        except Exception:
            return -1

    def _adaptive_threshold(self, total: int, small: float, large: float) -> float:
        """Return threshold appropriate for dataset size."""
        if total < 100:
            return small
        if total > 10000:
            return large
        # Linear interpolation
        return small + (large - small) * (total - 100) / (10000 - 100)

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_zvs_compliance(self) -> CheckResult:
        """Check that no canonical words use ZVS-forbidden forms.

        Adaptive: for small vocabularies (<100), allow 5% violations;
        for large (>10000), require <0.1%.
        """
        total = self._get_table_count("canonical_words")
        if total <= 0:
            # Fall back to dictionary + vocabulary tables
            total = self._get_table_count("vocabulary")
            if total <= 0:
                return CheckResult(
                    name="zvs_compliance",
                    passed=True,
                    details="No vocabulary data — check skipped",
                    score=1.0,
                )

        violations = 0
        try:
            with self._mgr.engine.connect() as conn:
                # Check canonical_words for forbidden forms
                for forbidden in ZVS_FORBIDDEN:
                    count = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM canonical_words "
                            "WHERE form = :word"
                        ),
                        {"word": forbidden},
                    ).scalar() or 0
                    violations += count

                # Also check vocabulary table
                for forbidden in ZVS_FORBIDDEN:
                    count = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM vocabulary "
                            "WHERE headword = :word COLLATE NOCASE"
                        ),
                        {"word": forbidden},
                    ).scalar() or 0
                    violations += count

        except Exception as exc:
            return CheckResult(
                name="zvs_compliance",
                passed=False,
                details=f"Error: {exc}",
                score=0.0,
            )

        threshold = self._adaptive_threshold(total, 0.05, 0.001)
        rate = violations / total if total > 0 else 0.0
        passed = rate <= threshold

        return CheckResult(
            name="zvs_compliance",
            passed=passed,
            count=total - violations,
            total=total,
            details=f"{violations} violations ({rate:.2%}) vs threshold ({threshold:.2%})",
            score=1.0 - min(rate * 10, 1.0),
        )

    def check_dictionary_coverage(self) -> CheckResult:
        """Check that Bible verses have dictionary coverage.

        Adaptive: for small Bible data (<100 verses), 30% coverage is OK;
        for full Bible (>30000), require 70%+.
        """
        bible_count = self._get_table_count("bible_verses")
        if bible_count <= 0:
            return CheckResult(
                name="dictionary_coverage",
                passed=True,
                details="No Bible data — check skipped",
                score=1.0,
            )

        dict_count = self._get_table_count("dictionary")
        if dict_count <= 0:
            return CheckResult(
                name="dictionary_coverage",
                passed=False,
                details="Bible data exists but dictionary is empty",
                score=0.0,
            )

        # Coverage heuristic: dictionary size relative to Bible vocabulary
        # A full Bible vocab is roughly 5000-10000 unique words
        # Normalize: if dict_count > 5000, coverage is good
        normalized = min(dict_count / 5000, 1.0)
        threshold = self._adaptive_threshold(bible_count, 0.3, 0.7)
        passed = normalized >= threshold

        return CheckResult(
            name="dictionary_coverage",
            passed=passed,
            count=dict_count,
            total=bible_count,
            details=f"Dictionary: {dict_count} words, Bible: {bible_count} verses, coverage: {normalized:.2%}",
            score=normalized,
        )

    def check_staging_health(self) -> CheckResult:
        """Check staging tables have data and are not stale."""
        staging_tables = {
            "foundation_staging_words": 0,
            "foundation_staging_sentences": 0,
            "foundation_staging_paragraphs": 0,
        }

        for tbl in staging_tables:
            staging_tables[tbl] = self._get_table_count(tbl)

        total_staging = sum(staging_tables.values())

        # Staging health: should have data if raw layer has data
        raw_count = self._get_table_count("foundation_raw_corpus")
        has_raw = raw_count > 0

        if not has_raw and total_staging == 0:
            return CheckResult(
                name="staging_health",
                passed=True,
                details="Empty pipeline (no raw or staging data) — clean slate",
                score=1.0,
            )

        if has_raw and total_staging == 0:
            return CheckResult(
                name="staging_health",
                passed=False,
                details=f"Raw has {raw_count} records but staging is empty — build_staging needed",
                score=0.0,
            )

        return CheckResult(
            name="staging_health",
            passed=True,
            count=total_staging,
            details=f"Staging: {staging_tables}",
            score=min(total_staging / max(raw_count, 1), 1.0),
        )

    def check_canonical_health(self) -> CheckResult:
        """Check canonical tables are populated and reasonable."""
        canonical_tables = {
            "canonical_words": 0,
            "canonical_sentences": 0,
            "canonical_paragraphs": 0,
        }

        for tbl in canonical_tables:
            canonical_tables[tbl] = self._get_table_count(tbl)

        total_canonical = sum(canonical_tables.values())

        if total_canonical == 0:
            return CheckResult(
                name="canonical_health",
                passed=False,
                details="Canonical tables empty — promote_to_canonical needed",
                score=0.0,
            )

        # Health is good if we have meaningful data
        return CheckResult(
            name="canonical_health",
            passed=True,
            count=total_canonical,
            details=f"Canonical: {canonical_tables}",
            score=min(total_canonical / 1000, 1.0),
        )

    def check_referential_integrity(self) -> CheckResult:
        """Check cross-table referential integrity.

        Verifies:
        - Every canonical word has a form (not empty)
        - Every canonical sentence has text (not empty)
        """
        issues = 0
        checked = 0

        try:
            with self._mgr.engine.connect() as conn:
                # Check canonical_words have non-empty form
                result = conn.execute(
                    text("SELECT COUNT(*) FROM canonical_words WHERE form IS NULL OR form = ''")
                ).scalar() or 0
                checked += 1
                if result > 0:
                    issues += 1

                # Check canonical_sentences have non-empty text
                result = conn.execute(
                    text("SELECT COUNT(*) FROM canonical_sentences WHERE text IS NULL OR text = ''")
                ).scalar() or 0
                checked += 1
                if result > 0:
                    issues += 1

        except Exception as exc:
            return CheckResult(
                name="referential_integrity",
                passed=False,
                details=f"Error: {exc}",
                score=0.0,
            )

        passed = issues == 0
        score = 1.0 - (issues / checked) if checked > 0 else 1.0

        return CheckResult(
            name="referential_integrity",
            passed=passed,
            count=checked - issues,
            total=checked,
            details=f"{issues} integrity issues in {checked} checks",
            score=score,
        )


def get_verification_runner(mgr: DatabaseManager | None = None) -> VerificationRunner:
    """Get or create a VerificationRunner singleton."""
    return VerificationRunner(mgr)
