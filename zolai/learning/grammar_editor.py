"""Grammar rule CRUD operations."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class GrammarEditor:
    """Manage grammar patterns in the database.

    Provides CRUD operations for grammar_patterns table.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def add_pattern(
        self,
        pattern: str,
        function: str,
        description: str = "",
        examples: str = "",
    ) -> dict[str, Any]:
        """Add a new grammar pattern.

        Args:
            pattern: The grammar pattern (e.g., "S + O + V").
            function: Function type (e.g., "sov", "negation", "question").
            description: Description of the pattern.
            examples: Example sentences (JSON or text).

        Returns:
            Dict with success status and pattern ID.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """INSERT INTO grammar_patterns
                   (pattern, function, description, examples)
                   VALUES (?, ?, ?, ?)""",
                (pattern, function, description, examples),
            )
            conn.commit()

            return {
                "success": True,
                "id": cur.lastrowid,
                "pattern": pattern,
                "function": function,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def update_pattern(
        self,
        pattern_id: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update a grammar pattern.

        Args:
            pattern_id: ID of the pattern to update.
            **kwargs: Fields to update (pattern, pattern_type, description, etc.).

        Returns:
            Dict with success status.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Build update query
            allowed_fields = {"pattern", "function", "description", "examples"}
            updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

            if not updates:
                return {"success": False, "error": "No valid fields to update"}

            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [pattern_id]

            cur.execute(
                f'UPDATE grammar_patterns SET {set_clause} WHERE id = ?',
                values,
            )
            conn.commit()

            return {
                "success": True,
                "id": pattern_id,
                "updated_fields": list(updates.keys()),
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def delete_pattern(self, pattern_id: int) -> dict[str, Any]:
        """Delete a grammar pattern."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute("DELETE FROM grammar_patterns WHERE id = ?", (pattern_id,))
            conn.commit()

            return {
                "success": True,
                "id": pattern_id,
                "deleted": cur.rowcount > 0,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def get_pattern(self, pattern_id: int) -> dict[str, Any] | None:
        """Get a grammar pattern by ID."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute("SELECT * FROM grammar_patterns WHERE id = ?", (pattern_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug("Get pattern failed: %s", e)
            return None
        finally:
            conn.close()

    def search_patterns(
        self,
        query: str | None = None,
        pattern_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search grammar patterns."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            conditions = []
            params = []

            if query:
                conditions.append("(pattern LIKE ? OR description LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%"])

            if pattern_type:
                conditions.append("pattern_type = ?")
                params.append(pattern_type)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            cur.execute(
                f"SELECT * FROM grammar_patterns WHERE {where_clause} LIMIT ?",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

        except Exception as e:
            logger.debug("Search patterns failed: %s", e)
            return []
        finally:
            conn.close()

    def _validate_zvs(self, text: str) -> dict[str, Any]:
        """Validate ZVS 2018 compliance."""
        from ..offline.rule_engine import ZVS_CORRECTIONS
        import re

        errors = []
        corrected = text

        for forbidden, correct in ZVS_CORRECTIONS.items():
            pattern = re.compile(rf"\b{forbidden}\b", re.IGNORECASE)
            if pattern.search(corrected):
                errors.append({
                    "forbidden": forbidden,
                    "correct": correct,
                })
                corrected = pattern.sub(correct, corrected)

        return {
            "is_compliant": len(errors) == 0,
            "errors": errors,
            "corrected_text": corrected,
        }
