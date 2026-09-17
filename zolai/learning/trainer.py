"""Correction-based context improvement (RAG-first, no fine-tuning)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class CorrectionTrainer:
    """Collect user corrections and build improved context prompts.

    This module does NOT fine-tune models. Instead, it:
    1. Collects user corrections
    2. Builds improved RAG context prompts
    3. Stores corrections for future reference

    This follows the RAG/embeddings-first invariant.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def record_correction(
        self,
        original_input: str,
        ai_response: str,
        corrected_response: str,
        context: str = "",
        user_id: str = "anonymous",
    ) -> dict[str, Any]:
        """Record a user correction.

        Args:
            original_input: The original user input.
            ai_response: The AI's response.
            corrected_response: The user's corrected version.
            context: Additional context.
            user_id: User who made the correction.

        Returns:
            Dict with success status and correction ID.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Store correction
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                (
                    "corrections",
                    0,
                    "correction",
                    ai_response,
                    corrected_response,
                    f"Correction by {user_id}: {original_input[:100]}",
                ),
            )

            # Store context for future RAG improvements
            if context:
                cur.execute(
                    """INSERT INTO data_audit_log
                       (table_name, row_id, field, old_value, new_value, changed_at, reason)
                       VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                    (
                        "context_improvements",
                        0,
                        "context",
                        original_input,
                        context,
                        f"Context for correction by {user_id}",
                    ),
                )

            conn.commit()

            return {
                "success": True,
                "correction_id": cur.lastrowid,
                "original_input": original_input,
                "ai_response": ai_response,
                "corrected_response": corrected_response,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def build_improved_context(
        self,
        user_input: str,
        base_context: str = "",
    ) -> str:
        """Build improved context prompt using past corrections.

        Args:
            user_input: Current user input.
            base_context: Base RAG context.

        Returns:
            Enhanced context string.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Find similar past corrections
            cur.execute(
                """SELECT old_value, new_value, reason
                   FROM data_audit_log
                   WHERE table_name = 'corrections'
                   AND field = 'correction'
                   ORDER BY changed_at DESC
                   LIMIT 10"""
            )
            corrections = cur.fetchall()

            # Build context
            context_parts = []

            if base_context:
                context_parts.append(base_context)

            if corrections:
                context_parts.append("\nPAST CORRECTIONS (learn from these):")
                for corr in corrections:
                    context_parts.append(
                        f"- Original: {corr['old_value'][:100]}\n"
                        f"  Corrected: {corr['new_value'][:100]}\n"
                        f"  Note: {corr['reason'][:100]}"
                    )

            # Add ZVS 2018 rules reminder
            context_parts.append("\nZVS 2018 RULES (always enforce):")
            context_parts.append("- pathian → pasian")
            context_parts.append("- ram → gam")
            context_parts.append("- fapa → tapa")
            context_parts.append("- bawipa → topa")
            context_parts.append("- siangpahrang → kumpipa")
            context_parts.append("- cu/cun → tua")
            context_parts.append("- Word order: SOV (Subject-Object-Verb)")
            context_parts.append("- Ergative marker: 'in' for transitive subjects")

            return "\n".join(context_parts)

        except Exception as e:
            logger.debug("Build improved context failed: %s", e)
            return base_context
        finally:
            conn.close()

    def get_correction_stats(self) -> dict[str, Any]:
        """Get statistics about corrections."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Total corrections
            cur.execute(
                "SELECT COUNT(*) FROM data_audit_log WHERE table_name = 'corrections'"
            )
            total = cur.fetchone()[0]

            # Recent corrections
            cur.execute(
                """SELECT changed_at, reason
                   FROM data_audit_log
                   WHERE table_name = 'corrections'
                   ORDER BY changed_at DESC
                   LIMIT 5"""
            )
            recent = [
                {"date": row["changed_at"], "note": row["reason"]}
                for row in cur.fetchall()
            ]

            # Common error patterns
            cur.execute(
                """SELECT new_value, COUNT(*) as count
                   FROM data_audit_log
                   WHERE table_name = 'corrections'
                   AND field = 'correction'
                   GROUP BY new_value
                   ORDER BY count DESC
                   LIMIT 10"""
            )
            patterns = [
                {"pattern": row["new_value"], "count": row["count"]}
                for row in cur.fetchall()
            ]

            return {
                "total_corrections": total,
                "recent_corrections": recent,
                "common_patterns": patterns,
            }

        except Exception as e:
            logger.debug("Get correction stats failed: %s", e)
            return {
                "total_corrections": 0,
                "recent_corrections": [],
                "common_patterns": [],
            }
        finally:
            conn.close()

    def get_improved_prompt(
        self,
        base_prompt: str,
        user_input: str,
    ) -> str:
        """Build improved system prompt using corrections.

        Args:
            base_prompt: Base system prompt.
            user_input: Current user input.

        Returns:
            Enhanced system prompt.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get recent corrections for similar inputs
            cur.execute(
                """SELECT old_value, new_value
                   FROM data_audit_log
                   WHERE table_name = 'corrections'
                   AND field = 'correction'
                   AND old_value LIKE ?
                   ORDER BY changed_at DESC
                   LIMIT 5""",
                (f"%{user_input[:50]}%",),
            )
            corrections = cur.fetchall()

            if not corrections:
                return base_prompt

            # Add correction examples to prompt
            prompt_additions = [
                "\n\nIMPORTANT CORRECTIONS (learn from these examples):"
            ]
            for corr in corrections:
                prompt_additions.append(
                    f"- Instead of: {corr['old_value'][:100]}\n"
                    f"  Use: {corr['new_value'][:100]}"
                )

            return base_prompt + "\n".join(prompt_additions)

        except Exception as e:
            logger.debug("Get improved prompt failed: %s", e)
            return base_prompt
        finally:
            conn.close()
