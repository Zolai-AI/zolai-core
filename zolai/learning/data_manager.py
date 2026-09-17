"""Data management for importing/exporting datasets."""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class DataManager:
    """Import/export datasets to/from canonical SQLite tables.

    Supports JSONL and CSV formats with deduplication.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def import_jsonl(
        self,
        filepath: str | Path,
        table: str,
        dedup_key: str | None = None,
    ) -> dict[str, Any]:
        """Import JSONL file into a database table.

        Args:
            filepath: Path to JSONL file.
            table: Target table name.
            dedup_key: Column name for deduplication.

        Returns:
            Dict with import statistics.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            return {"success": False, "error": f"File not found: {filepath}"}

        conn = self._get_connection()
        cur = conn.cursor()

        imported = 0
        skipped = 0
        errors = 0

        try:
            with open(filepath, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        record = json.loads(line.strip())
                        if not record:
                            continue

                        # Dedup check
                        if dedup_key and dedup_key in record:
                            cur.execute(
                                f'SELECT COUNT(*) FROM "{table}" WHERE {dedup_key} = ?',
                                (record[dedup_key],),
                            )
                            if cur.fetchone()[0] > 0:
                                skipped += 1
                                continue

                        # Insert record
                        columns = list(record.keys())
                        placeholders = ", ".join(["?" for _ in columns])
                        column_names = ", ".join([f'"{c}"' for c in columns])
                        values = [record[c] for c in columns]

                        cur.execute(
                            f'INSERT INTO "{table}" ({column_names}) VALUES ({placeholders})',
                            values,
                        )
                        imported += 1

                    except json.JSONDecodeError:
                        errors += 1
                        logger.warning("Invalid JSON at line %d", line_num)
                    except Exception as e:
                        errors += 1
                        logger.warning("Error at line %d: %s", line_num, e)

            conn.commit()
            return {
                "success": True,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "table": table,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def export_jsonl(
        self,
        table: str,
        filepath: str | Path,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Export database table to JSONL file.

        Args:
            table: Source table name.
            filepath: Output JSONL file path.
            where: Optional WHERE clause.
            limit: Maximum rows to export.

        Returns:
            Dict with export statistics.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_connection()
        cur = conn.cursor()

        try:
            query = f'SELECT * FROM "{table}"'
            if where:
                query += f" WHERE {where}"
            if limit:
                query += f" LIMIT {limit}"

            cur.execute(query)
            rows = cur.fetchall()

            with open(filepath, "w", encoding="utf-8") as f:
                for row in rows:
                    record = dict(row)
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

            return {
                "success": True,
                "exported": len(rows),
                "table": table,
                "filepath": str(filepath),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def import_csv(
        self,
        filepath: str | Path,
        table: str,
        dedup_key: str | None = None,
    ) -> dict[str, Any]:
        """Import CSV file into a database table."""
        filepath = Path(filepath)
        if not filepath.exists():
            return {"success": False, "error": f"File not found: {filepath}"}

        conn = self._get_connection()
        cur = conn.cursor()

        imported = 0
        skipped = 0
        errors = 0

        try:
            with open(filepath, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row_num, record in enumerate(reader, 1):
                    try:
                        # Dedup check
                        if dedup_key and dedup_key in record:
                            cur.execute(
                                f'SELECT COUNT(*) FROM "{table}" WHERE {dedup_key} = ?',
                                (record[dedup_key],),
                            )
                            if cur.fetchone()[0] > 0:
                                skipped += 1
                                continue

                        # Insert record
                        columns = list(record.keys())
                        placeholders = ", ".join(["?" for _ in columns])
                        column_names = ", ".join([f'"{c}"' for c in columns])
                        values = [record[c] for c in columns]

                        cur.execute(
                            f'INSERT INTO "{table}" ({column_names}) VALUES ({placeholders})',
                            values,
                        )
                        imported += 1

                    except Exception as e:
                        errors += 1
                        logger.warning("Error at row %d: %s", row_num, e)

            conn.commit()
            return {
                "success": True,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
                "table": table,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def export_csv(
        self,
        table: str,
        filepath: str | Path,
        where: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Export database table to CSV file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        conn = self._get_connection()
        cur = conn.cursor()

        try:
            query = f'SELECT * FROM "{table}"'
            if where:
                query += f" WHERE {where}"
            if limit:
                query += f" LIMIT {limit}"

            cur.execute(query)
            rows = cur.fetchall()

            if not rows:
                return {"success": True, "exported": 0, "table": table}

            columns = [desc[0] for desc in cur.description]

            with open(filepath, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row))

            return {
                "success": True,
                "exported": len(rows),
                "table": table,
                "filepath": str(filepath),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def merge_with_dedup(
        self,
        source_table: str,
        target_table: str,
        dedup_key: str,
    ) -> dict[str, Any]:
        """Merge source table into target with deduplication."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get source rows
            cur.execute(f'SELECT * FROM "{source_table}"')
            source_rows = cur.fetchall()

            merged = 0
            skipped = 0

            for row in source_rows:
                record = dict(row)
                key_value = record.get(dedup_key)

                if key_value:
                    cur.execute(
                        f'SELECT COUNT(*) FROM "{target_table}" WHERE {dedup_key} = ?',
                        (key_value,),
                    )
                    if cur.fetchone()[0] > 0:
                        skipped += 1
                        continue

                columns = list(record.keys())
                placeholders = ", ".join(["?" for _ in columns])
                column_names = ", ".join([f'"{c}"' for c in columns])
                values = [record[c] for c in columns]

                cur.execute(
                    f'INSERT INTO "{target_table}" ({column_names}) VALUES ({placeholders})',
                    values,
                )
                merged += 1

            conn.commit()
            return {
                "success": True,
                "merged": merged,
                "skipped": skipped,
                "source": source_table,
                "target": target_table,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
