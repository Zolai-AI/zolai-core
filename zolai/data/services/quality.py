"""Quality service for duplicate detection and quality reports."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


class QualityService:
    """Service for data quality checks, duplicate detection, and reports."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def check_duplicates(
        self, table: str, columns: list[str]
    ) -> dict[str, Any]:
        """Find duplicate rows based on column combination."""
        col_list = ", ".join(columns)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT {col_list}, COUNT(*) as cnt FROM {table} "
                    f"GROUP BY {col_list} HAVING cnt > 1 "
                    f"ORDER BY cnt DESC LIMIT 100"
                )
            ).fetchall()

        duplicates = []
        total_dup_rows = 0
        for row in rows:
            dup_data = dict(zip(columns, row[:-1]))
            dup_data["count"] = row[-1]
            duplicates.append(dup_data)
            total_dup_rows += row[-1]

        return {
            "table": table,
            "columns": columns,
            "duplicate_groups": len(duplicates),
            "total_duplicate_rows": total_dup_rows,
            "examples": duplicates[:10],
        }

    def check_null_counts(self, table: str) -> dict[str, int]:
        """Count null/empty values per column."""
        with self.engine.connect() as conn:
            # Get column names
            cols = conn.execute(
                text(f"PRAGMA table_info({table})")
            ).fetchall()
            col_names = [c[1] for c in cols]

            null_counts = {}
            for col in col_names:
                count = conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL "
                        f"OR TRIM(CAST({col} AS TEXT)) = ''"
                    )
                ).scalar()
                null_counts[col] = count or 0

        return null_counts

    def check_dictionary_quality(self) -> dict[str, Any]:
        """Comprehensive dictionary quality report."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM dictionary")).scalar()
            missing_myanmar = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dictionary WHERE myanmar IS NULL OR myanmar = ''"
                )
            ).scalar()
            missing_english_clean = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dictionary WHERE english_clean IS NULL OR english_clean = ''"
                )
            ).scalar()
            missing_pos = conn.execute(
                text("SELECT COUNT(*) FROM dictionary WHERE pos = '' OR pos IS NULL")
            ).scalar()
            unique_sources = conn.execute(
                text("SELECT COUNT(DISTINCT source) FROM dictionary WHERE source != ''")
            ).scalar()

            # ZVS violations
            zvs_violations = 0
            for forbidden in [
                "pathian", "ram", "fapa", "bawipa", "siangpahrang",
                "cu", "cun", "suah", "zalenna", "nunnak"
            ]:
                count = conn.execute(
                    text(f"SELECT COUNT(*) FROM dictionary WHERE zolai LIKE '%{forbidden}%'")
                ).scalar()
                zvs_violations += count or 0

        return {
            "total_entries": total or 0,
            "missing_myanmar": missing_myanmar or 0,
            "missing_english_clean": missing_english_clean or 0,
            "missing_pos": missing_pos or 0,
            "unique_sources": unique_sources or 0,
            "zvs_violations": zvs_violations,
            "completeness_myanmar": round(
                (1 - (missing_myanmar or 0) / (total or 1)) * 100, 1
            ),
            "completeness_english_clean": round(
                (1 - (missing_english_clean or 0) / (total or 1)) * 100, 1
            ),
        }

    def check_bible_quality(self) -> dict[str, Any]:
        """Comprehensive Bible quality report."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM bible_verses")).scalar()
            books = conn.execute(
                text("SELECT COUNT(DISTINCT book) FROM bible_verses")
            ).scalar()
            missing_zo_tdb77 = conn.execute(
                text("SELECT COUNT(*) FROM bible_verses WHERE zo_tdb77 IS NULL OR zo_tdb77 = ''")
            ).scalar()
            missing_zo_tedim = conn.execute(
                text("SELECT COUNT(*) FROM bible_verses WHERE zo_tedim2010 IS NULL OR zo_tedim2010 = ''")
            ).scalar()
            missing_en = conn.execute(
                text("SELECT COUNT(*) FROM bible_verses WHERE en_kJV IS NULL OR en_kJV = ''")
            ).scalar()
            missing_my = conn.execute(
                text("SELECT COUNT(*) FROM bible_verses WHERE myanmar IS NULL OR myanmar = ''")
            ).scalar()

            # Check for duplicates
            dupes = conn.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT ref, COUNT(*) as cnt FROM bible_verses "
                    "GROUP BY ref HAVING cnt > 1)"
                )
            ).scalar()

        return {
            "total_verses": total or 0,
            "books": books or 0,
            "missing_zo_tdb77": missing_zo_tdb77 or 0,
            "missing_zo_tedim2010": missing_zo_tedim or 0,
            "missing_english": missing_en or 0,
            "missing_myanmar": missing_my or 0,
            "duplicate_refs": dupes or 0,
            "completeness_zo_tdb77": round(
                (1 - (missing_zo_tdb77 or 0) / (total or 1)) * 100, 1
            ),
            "completeness_english": round(
                (1 - (missing_en or 0) / (total or 1)) * 100, 1
            ),
        }

    def check_translation_quality(self) -> dict[str, Any]:
        """Translation pairs quality report."""
        with self.engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM translations")).scalar()
            low_conf = conn.execute(
                text("SELECT COUNT(*) FROM translations WHERE confidence < 0.5")
            ).scalar()
            by_direction = conn.execute(
                text("SELECT direction, COUNT(*) FROM translations GROUP BY direction")
            ).fetchall()

        return {
            "total_pairs": total or 0,
            "low_confidence": low_conf or 0,
            "by_direction": dict(by_direction),
        }

    def full_quality_report(self) -> dict[str, Any]:
        """Generate full quality report across all tables."""
        return {
            "dictionary": self.check_dictionary_quality(),
            "bible": self.check_bible_quality(),
            "translations": self.check_translation_quality(),
            "dictionary_en_zo": self.check_null_counts("dictionary_en_zo"),
            "vocab": self.check_null_counts("vocab"),
            "phrases": self.check_null_counts("phrases"),
            "grammar_patterns": self.check_null_counts("grammar_patterns"),
            "word_usage": self.check_null_counts("word_usage"),
            "training_exercises": self.check_null_counts("training_exercises"),
            "provenance": self.check_null_counts("provenance"),
        }
