"""
Corpus Validator - Validates and checks quality of the corpus.

Quality reports are stored in SQLite (``corpus_quality_reports`` /
``corpus_quality_issues``); nothing is written to a JSON file.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.shared.clock import stamp

from ...data.repositories import get_engine

logger = logging.getLogger(__name__)

#: Canonical book abbreviations (Zolai corpus convention).
KNOWN_BOOKS = frozenset({
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
    "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
    "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
    "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL",
    "MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH",
    "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS",
    "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
})

#: Approximate verse counts per book used for the completeness check.
EXPECTED_VERSE_COUNTS = {
    "GEN": 1533, "EXO": 1213, "LEV": 859, "NUM": 1288, "DEU": 959,
    "JOS": 658, "JDG": 618, "RUT": 85, "1SA": 810, "2SA": 695,
    "1KI": 816, "2KI": 719, "1CH": 942, "2CH": 822, "EZR": 280,
    "NEH": 406, "EST": 167, "JOB": 1070, "PSA": 2461, "PRO": 915,
    "ECC": 222, "SNG": 117, "ISA": 1292, "JER": 1364, "LAM": 154,
    "EZK": 1273, "DAN": 357, "HOS": 197, "JOL": 73, "AMO": 146,
    "OBA": 21, "JON": 48, "MIC": 105, "NAM": 47, "HAB": 56,
    "ZEP": 53, "HAG": 38, "ZEC": 211, "MAL": 55,
    "MAT": 1071, "MRK": 678, "LUK": 1151, "JHN": 879, "ACT": 1007,
    "ROM": 433, "1CO": 437, "2CO": 257, "GAL": 149, "EPH": 155,
    "PHP": 104, "COL": 95, "1TH": 89, "2TH": 47, "1TI": 183,
    "2TI": 83, "TIT": 46, "PHM": 25, "HEB": 303, "JAS": 108,
    "1PE": 105, "2PE": 61, "1JN": 105, "2JN": 13, "3JN": 14,
    "JUD": 25, "REV": 404,
}


@dataclass
class QualityIssue:
    """A quality issue found in the corpus."""

    issue_type: str
    severity: str  # critical, high, medium, low
    message: str
    ref: str = ""
    details: dict = field(default_factory=dict)
    suggested_fix: str = ""


@dataclass
class QualityReport:
    """Quality check report."""

    total_checked: int = 0
    issues: list = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    stats: dict = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return self.critical_count > 0

    def add_issue(self, issue: QualityIssue):
        """Record an issue and bump its severity counter."""
        self.issues.append(issue)
        if issue.severity == "critical":
            self.critical_count += 1
        elif issue.severity == "high":
            self.high_count += 1
        elif issue.severity == "medium":
            self.medium_count += 1
        elif issue.severity == "low":
            self.low_count += 1

    def as_dict(self) -> dict:
        """Serialise the report to a plain dict."""
        return {
            "summary": {
                "total_checked": self.total_checked,
                "critical": self.critical_count,
                "high": self.high_count,
                "medium": self.medium_count,
                "low": self.low_count,
                "total_issues": len(self.issues),
            },
            "stats": self.stats,
            "issues": [
                {
                    "type": i.issue_type,
                    "severity": i.severity,
                    "message": i.message,
                    "ref": i.ref,
                    "details": i.details,
                    "suggested_fix": i.suggested_fix,
                }
                for i in self.issues
            ],
        }


def has_control_chars(value: str) -> bool:
    """True when the string holds a C0/C1 control character (tab/newline aside)."""
    if not value:
        return False
    return any(ord(ch) < 32 and ch not in "\t\n\r" or 127 <= ord(ch) <= 159 for ch in value)


class CorpusValidator:
    """Validates corpus quality and consistency."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def validate_all(self, zvs_limit: int = 1000) -> QualityReport:
        """Run all validation checks."""
        report = QualityReport()

        logger.info("Starting corpus validation...")

        # Check 1: Missing translations
        self._check_missing_translations(report)

        # Check 2: Duplicate verses
        self._check_duplicates(report)

        # Check 3: Encoding issues
        self._check_encoding(report)

        # Check 4: ZVS compliance
        self._check_zvs_compliance(report, limit=zvs_limit)

        # Check 5: Consistency
        self._check_consistency(report)

        # Check 6: Empty or very short entries
        self._check_empty_entries(report)

        # Check 7: Verse numbering
        self._check_verse_numbering(report)

        # Check 8: Bible structure
        self._check_bible_structure(report)

        # Summary
        report.stats = self._get_summary_stats()
        report.total_checked = self._get_total_verses()

        logger.info(
            f"Validation complete: {report.critical_count} critical, "
            f"{report.high_count} high, {report.medium_count} medium, "
            f"{report.low_count} low issues"
        )

        return report

    def _check_missing_translations(self, report: QualityReport):
        """Check for missing translations."""
        with self.engine.connect() as conn:
            # Verses with Zolai but no English
            missing_en = conn.execute(text("""
                SELECT ref, zo_tdb77 FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
                AND (en_kJV IS NULL OR en_kJV = '')
                LIMIT 100
            """)).fetchall()

            for row in missing_en:
                report.add_issue(QualityIssue(
                    issue_type="missing_translation",
                    severity="high",
                    message=f"Verse {row.ref} has Zolai but no English translation",
                    ref=row.ref,
                    details={"has_zo": True, "has_en": False},
                    suggested_fix="Add English translation",
                ))

            # Verses with English but no Zolai
            missing_zo = conn.execute(text("""
                SELECT ref, en_kJV FROM bible_verses
                WHERE en_kJV IS NOT NULL AND en_kJV != ''
                AND (zo_tdb77 IS NULL OR zo_tdb77 = '')
                LIMIT 100
            """)).fetchall()

            for row in missing_zo:
                report.add_issue(QualityIssue(
                    issue_type="missing_translation",
                    severity="high",
                    message=f"Verse {row.ref} has English but no Zolai translation",
                    ref=row.ref,
                    details={"has_en": True, "has_zo": False},
                    suggested_fix="Add Zolai translation",
                ))

    def _check_duplicates(self, report: QualityReport):
        """Check for duplicate verses."""
        with self.engine.connect() as conn:
            # Duplicate refs
            duplicates = conn.execute(text("""
                SELECT ref, COUNT(*) as cnt FROM bible_verses
                GROUP BY ref HAVING COUNT(*) > 1
            """)).fetchall()

            for row in duplicates:
                report.add_issue(QualityIssue(
                    issue_type="duplicate_ref",
                    severity="critical",
                    message=f"Duplicate reference {row.ref} appears {row.cnt} times",
                    ref=row.ref,
                    details={"count": row.cnt},
                    suggested_fix="Remove duplicate entries",
                ))

            # Duplicate content (same Zolai text)
            dup_content = conn.execute(text("""
                SELECT zo_tdb77, COUNT(*) as cnt, GROUP_CONCAT(ref) as refs
                FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
                GROUP BY zo_tdb77 HAVING COUNT(*) > 1
            """)).fetchall()

            for row in dup_content:
                report.add_issue(QualityIssue(
                    issue_type="duplicate_content",
                    severity="medium",
                    message=f"Duplicate Zolai text: {row.zo_tdb77[:50]}... in {row.cnt} verses",
                    ref=(row.refs or "").split(",")[0],
                    details={"refs": row.refs, "count": row.cnt},
                    suggested_fix="Review and consolidate duplicates",
                ))

    def _check_encoding(self, report: QualityReport):
        """Check for encoding issues.

        SQLite has no ``REGEXP`` operator, so the control-character scan runs
        Python-side over the Zolai text instead of in SQL.
        """
        with self.engine.connect() as conn:
            # Replacement characters (U+FFFD) are matched in SQL.
            encoding_issues = conn.execute(text("""
                SELECT ref, zo_tdb77 FROM bible_verses
                WHERE zo_tdb77 LIKE '%�%'
                LIMIT 100
            """)).fetchall()

            for row in encoding_issues:
                report.add_issue(QualityIssue(
                    issue_type="encoding_error",
                    severity="high",
                    message=f"Encoding issue in verse {row.ref}",
                    ref=row.ref,
                    details={"text": (row.zo_tdb77 or "")[:100]},
                    suggested_fix="Re-encode from source",
                ))

            # Control characters need a Python-side scan.
            candidates = conn.execute(text("""
                SELECT ref, zo_tdb77 FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
            """)).fetchall()

            found = 0
            for row in candidates:
                if has_control_chars(row.zo_tdb77):
                    report.add_issue(QualityIssue(
                        issue_type="control_characters",
                        severity="medium",
                        message=f"Control characters in verse {row.ref}",
                        ref=row.ref,
                        suggested_fix="Remove control characters",
                    ))
                    found += 1
                    if found >= 100:
                        break

    def _check_zvs_compliance(self, report: QualityReport, limit: int = 1000):
        """Check ZVS 2018 compliance."""
        # Use the existing ZVS validator
        from ...zvs import validate

        with self.engine.connect() as conn:
            verses = conn.execute(text("""
                SELECT ref, zo_tdb77 FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
                LIMIT :limit
            """), {"limit": limit}).fetchall()

            zvs_issues = 0
            for row in verses:
                report_result = validate(row.zo_tdb77, source="bible", context="scripture")
                if not report_result.is_valid:
                    zvs_issues += 1
                    if zvs_issues <= 50:  # Limit reported issues
                        violations = [
                            f"{v.rule_id}: {v.forbidden}->{v.preferred}"
                            for v in report_result.violations
                        ]
                        report.add_issue(QualityIssue(
                            issue_type="zvs_violation",
                            severity="medium",
                            message=f"ZVS violation in {row.ref}: {'; '.join(violations[:3])}",
                            ref=row.ref,
                            details={"violations": violations},
                            suggested_fix="Replace with ZVS-compliant forms",
                        ))

            if zvs_issues > 50:
                report.add_issue(QualityIssue(
                    issue_type="zvs_violation",
                    severity="high",
                    message=f"Total ZVS violations: {zvs_issues} (showing first 50)",
                    details={"total_violations": zvs_issues},
                    suggested_fix="Batch correct ZVS violations",
                ))

    def _check_consistency(self, report: QualityReport):
        """Check internal consistency."""
        with self.engine.connect() as conn:
            # Check for inconsistent book names
            books = conn.execute(text("""
                SELECT DISTINCT book, COUNT(*) as cnt FROM bible_verses
                GROUP BY book ORDER BY book
            """)).fetchall()

            for row in books:
                if row.book not in KNOWN_BOOKS:
                    report.add_issue(QualityIssue(
                        issue_type="unknown_book",
                        severity="medium",
                        message=f"Unknown book abbreviation: {row.book}",
                        details={"book": row.book, "verse_count": row.cnt},
                        suggested_fix="Verify book abbreviation",
                    ))

    def _check_empty_entries(self, report: QualityReport):
        """Check for empty or very short entries."""
        with self.engine.connect() as conn:
            # Empty Zolai
            empty_zo = conn.execute(text("""
                SELECT ref FROM bible_verses
                WHERE zo_tdb77 IS NULL OR zo_tdb77 = '' OR zo_tdb77 = ' '
                LIMIT 100
            """)).fetchall()

            for row in empty_zo:
                report.add_issue(QualityIssue(
                    issue_type="empty_entry",
                    severity="high",
                    message=f"Empty Zolai text in {row.ref}",
                    ref=row.ref,
                    suggested_fix="Add Zolai translation",
                ))

            # Very short entries (< 3 chars)
            short_zo = conn.execute(text("""
                SELECT ref, zo_tdb77 FROM bible_verses
                WHERE LENGTH(TRIM(zo_tdb77)) < 3
                LIMIT 100
            """)).fetchall()

            for row in short_zo:
                report.add_issue(QualityIssue(
                    issue_type="short_entry",
                    severity="low",
                    message=f"Very short Zolai text in {row.ref}: '{row.zo_tdb77}'",
                    ref=row.ref,
                    suggested_fix="Verify and expand translation",
                ))

    def _check_verse_numbering(self, report: QualityReport):
        """Check verse numbering consistency."""
        with self.engine.connect() as conn:
            # Check for gaps in verse numbering
            gaps = conn.execute(text("""
                SELECT book, chapter,
                       MIN(verse) as min_v, MAX(verse) as max_v,
                       MAX(verse) - MIN(verse) + 1 - COUNT(*) as gaps
                FROM bible_verses
                GROUP BY book, chapter
                HAVING MAX(verse) - MIN(verse) + 1 - COUNT(*) > 0
            """)).fetchall()

            for row in gaps:
                report.add_issue(QualityIssue(
                    issue_type="verse_gap",
                    severity="low",
                    message=f"Verse gap in {row.book} {row.chapter}: {row.gaps} missing verses",
                    ref=f"{row.book} {row.chapter}",
                    details={"min_verse": row.min_v, "max_verse": row.max_v, "gaps": row.gaps},
                    suggested_fix="Check for missing verses",
                ))

    def _check_bible_structure(self, report: QualityReport):
        """Check overall Bible structure."""
        with self.engine.connect() as conn:
            actual = conn.execute(text("""
                SELECT book, COUNT(*) as cnt FROM bible_verses
                GROUP BY book ORDER BY book
            """)).fetchall()

            for row in actual:
                expected = EXPECTED_VERSE_COUNTS.get(row.book, 0)
                if expected > 0:
                    diff = abs(row.cnt - expected)
                    pct = diff / expected * 100
                    if pct > 10:  # More than 10% difference
                        severity = "high" if pct > 50 else "medium"
                        report.add_issue(QualityIssue(
                            issue_type="verse_count_mismatch",
                            severity=severity,
                            message=(
                                f"Verse count mismatch for {row.book}: "
                                f"{row.cnt} vs expected {expected} ({pct:.1f}% diff)"
                            ),
                            ref=row.book,
                            details={"actual": row.cnt, "expected": expected, "diff_pct": pct},
                            suggested_fix="Verify verse count and completeness",
                        ))

    def _get_summary_stats(self) -> dict:
        """Get summary statistics."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar()
            with_zo = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
            """)).scalar()
            with_en = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE en_kJV IS NOT NULL AND en_kJV != ''
            """)).scalar()
            with_both = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND en_kJV IS NOT NULL
            """)).scalar()

            return {
                "total_verses": total,
                "with_zolai": with_zo,
                "with_english": with_en,
                "parallel": with_both,
                "coverage_zolai": with_zo / total * 100 if total else 0,
                "coverage_english": with_en / total * 100 if total else 0,
                "coverage_both": with_both / total * 100 if total else 0,
            }

    def _get_total_verses(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar()

    def save_report(self, report: QualityReport) -> str:
        """Persist a quality report to SQLite; returns the report id.

        Idempotent: the id is a hash of the report content, so saving the same
        report twice leaves exactly one copy in the database.
        """
        data = report.as_dict()
        digest = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        report_id = f"qr_{digest}"
        now = stamp()

        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS corpus_quality_reports (
                    report_id TEXT PRIMARY KEY,
                    total_checked INTEGER NOT NULL,
                    critical_count INTEGER NOT NULL,
                    high_count INTEGER NOT NULL,
                    medium_count INTEGER NOT NULL,
                    low_count INTEGER NOT NULL,
                    stats TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS corpus_quality_issues (
                    issue_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    issue_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    ref TEXT,
                    details TEXT,
                    suggested_fix TEXT
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_corpus_quality_issues_report
                ON corpus_quality_issues (report_id)
            """))

            conn.execute(
                text("""
                    INSERT OR IGNORE INTO corpus_quality_reports
                        (report_id, total_checked, critical_count, high_count,
                         medium_count, low_count, stats, created_at)
                    VALUES (:report_id, :total_checked, :critical, :high,
                            :medium, :low, :stats, :created_at)
                """),
                {
                    "report_id": report_id,
                    "total_checked": report.total_checked,
                    "critical": report.critical_count,
                    "high": report.high_count,
                    "medium": report.medium_count,
                    "low": report.low_count,
                    "stats": json.dumps(report.stats, sort_keys=True, default=str),
                    "created_at": now,
                },
            )

            if data["issues"]:
                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO corpus_quality_issues
                            (issue_id, report_id, issue_type, severity, message,
                             ref, details, suggested_fix)
                        VALUES (:issue_id, :report_id, :issue_type, :severity, :message,
                                :ref, :details, :suggested_fix)
                    """),
                    [
                        {
                            "issue_id": f"{report_id}:{idx}",
                            "report_id": report_id,
                            "issue_type": issue["type"],
                            "severity": issue["severity"],
                            "message": issue["message"],
                            "ref": issue["ref"],
                            "details": json.dumps(issue["details"], default=str),
                            "suggested_fix": issue["suggested_fix"],
                        }
                        for idx, issue in enumerate(data["issues"])
                    ],
                )

        logger.info(f"Saved quality report {report_id} ({len(data['issues'])} issues)")
        return report_id

    def get_report(self, report_id: str) -> dict | None:
        """Read a stored report (summary + stats) back."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT report_id, total_checked, critical_count, high_count,
                           medium_count, low_count, stats, created_at
                    FROM corpus_quality_reports WHERE report_id = :report_id
                """),
                {"report_id": report_id},
            ).fetchone()
            if not row:
                return None
            report = dict(row._mapping)
            report["stats"] = json.loads(report["stats"])
            return report

    def count_issues(self, report_id: str = None) -> int:
        """Count stored issues, optionally for one report."""
        sql = "SELECT COUNT(*) FROM corpus_quality_issues"
        params: dict = {}
        if report_id:
            sql += " WHERE report_id = :report_id"
            params["report_id"] = report_id
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()


class QualityChecker:
    """Quick quality checks for specific operations."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()

    def quick_check(self, ref: str) -> dict:
        """Quick quality check for a single verse."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT ref, zo_tdb77, en_kJV, zo_tedim2010
                    FROM bible_verses WHERE ref = :ref
                """),
                {"ref": ref},
            ).fetchone()

            if not row:
                return {"valid": False, "error": "Verse not found"}

            issues = []

            if not row.zo_tdb77:
                issues.append("Missing Zolai text")
            if not row.en_kJV:
                issues.append("Missing English translation")
            if len(row.zo_tdb77 or "") < 3:
                issues.append("Zolai text too short")

            return {
                "ref": ref,
                "valid": len(issues) == 0,
                "issues": issues,
                "has_zo": bool(row.zo_tdb77),
                "has_en": bool(row.en_kJV),
                "has_tedim": bool(row.zo_tedim2010),
                "zo_length": len(row.zo_tdb77 or ""),
                "en_length": len(row.en_kJV or ""),
            }

    def check_batch(self, refs: list) -> list:
        """Check multiple verses."""
        return [self.quick_check(ref) for ref in refs]

    def get_corpus_health(self) -> dict:
        """Get overall corpus health score."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar()
            valid = conn.execute(text("""
                SELECT COUNT(*) FROM bible_verses
                WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
                AND en_kJV IS NOT NULL AND en_kJV != ''
            """)).scalar()

            health_score = valid / total * 100 if total > 0 else 0

            return {
                "total_verses": total,
                "complete_pairs": valid,
                "health_score": health_score,
                "status": "healthy" if health_score > 90 else "needs_attention" if health_score > 70 else "critical",
            }
