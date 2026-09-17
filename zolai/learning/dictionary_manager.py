"""Dictionary CRUD operations."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class DictionaryManager:
    """Manage dictionary entries in the database.

    Provides CRUD operations for dictionary and dictionary_en_zo tables.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def add_entry(
        self,
        zolai: str,
        english: str,
        myanmar: str = "",
        pos: str = "",
        source: str = "user_add",
    ) -> dict[str, Any]:
        """Add a new dictionary entry.

        Args:
            zolai: Zolai word.
            english: English translation.
            myanmar: Myanmar translation (optional).
            pos: Part of speech.
            source: Source of the entry.

        Returns:
            Dict with success status.
        """
        # Check for duplicates
        if self._is_duplicate(zolai, english):
            return {
                "success": False,
                "error": "Duplicate entry",
                "zolai": zolai,
                "english": english,
            }

        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Insert into ZO→EN table
            cur.execute(
                """INSERT INTO dictionary
                   (zolai, english_clean, pos, source)
                   VALUES (?, ?, ?, ?)""",
                (zolai.lower(), english, pos, source),
            )

            # Insert into EN→ZO table
            cur.execute(
                """INSERT INTO dictionary_en_zo
                   (headword, translations_clean, pos, source)
                   VALUES (?, ?, ?, ?)""",
                (english.lower(), zolai, pos, source),
            )

            # Log to audit
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                ("dictionary", cur.lastrowid, "entry", "", f"{zolai}: {english}", f"Added by {source}"),
            )

            conn.commit()
            return {
                "success": True,
                "zolai": zolai,
                "english": english,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def update_entry(
        self,
        zolai: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update a dictionary entry.

        Args:
            zolai: Zolai word to update.
            **kwargs: Fields to update (english, myanmar, pos).

        Returns:
            Dict with success status.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get old values for audit
            cur.execute("SELECT * FROM dictionary WHERE zolai = ?", (zolai.lower(),))
            old_row = cur.fetchone()
            if not old_row:
                return {"success": False, "error": "Entry not found"}

            old_values = dict(old_row)

            # Build update for ZO→EN
            if "english" in kwargs:
                cur.execute(
                    "UPDATE dictionary SET english_clean = ? WHERE zolai = ?",
                    (kwargs["english"], zolai.lower()),
                )

            if "pos" in kwargs:
                cur.execute(
                    "UPDATE dictionary SET pos = ? WHERE zolai = ?",
                    (kwargs["pos"], zolai.lower()),
                )

            # Log to audit
            for field, new_value in kwargs.items():
                old_value = old_values.get(field, "")
                cur.execute(
                    """INSERT INTO data_audit_log
                       (table_name, row_id, field, old_value, new_value, changed_at, reason)
                       VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                    ("dictionary", 0, field, str(old_value), str(new_value), "Updated via DictionaryManager"),
                )

            conn.commit()
            return {
                "success": True,
                "zolai": zolai,
                "updated_fields": list(kwargs.keys()),
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def delete_entry(
        self,
        zolai: str,
        deleted_by: str = "user",
        reason: str = "user_delete",
    ) -> dict[str, Any]:
        """Soft delete a dictionary entry."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get entry before deletion
            cur.execute("SELECT * FROM dictionary WHERE zolai = ?", (zolai.lower(),))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": "Entry not found"}

            # Soft delete
            cur.execute(
                "UPDATE dictionary SET is_deleted = 1, deleted_at = datetime('now') WHERE zolai = ?",
                (zolai.lower(),),
            )

            # Log to audit
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                ("dictionary", 0, "is_deleted", "0", "1", f"Deleted by {deleted_by}: {reason}"),
            )

            conn.commit()
            return {
                "success": True,
                "zolai": zolai,
                "deleted_by": deleted_by,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def search(
        self,
        query: str,
        direction: str = "both",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search dictionary.

        Args:
            query: Search term.
            direction: "zo-en", "en-zo", or "both".
            limit: Maximum results.

        Returns:
            List of matching entries.
        """
        results = []

        if direction in ("zo-en", "both"):
            results.extend(self._search_zo_en(query, limit))

        if direction in ("en-zo", "both"):
            results.extend(self._search_en_zo(query, limit))

        return results[:limit]

    def _search_zo_en(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Search ZO→EN."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT zolai, english_clean, english, pos, source
                   FROM dictionary
                   WHERE zolai LIKE ? OR english_clean LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )
            return [
                {
                    "zolai": row["zolai"],
                    "english": row["english_clean"] or row["english"],
                    "pos": row["pos"],
                    "source": row["source"],
                    "direction": "zo-en",
                }
                for row in cur.fetchall()
            ]
        except Exception as e:
            logger.debug("ZO→EN search failed: %s", e)
            return []
        finally:
            conn.close()

    def _search_en_zo(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Search EN→ZO."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT headword, translations_clean, translations, pos, source
                   FROM dictionary_en_zo
                   WHERE headword LIKE ? OR translations_clean LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )

            results = []
            for row in cur.fetchall():
                trans = row["translations_clean"]
                if not trans:
                    try:
                        trans_list = json.loads(row["translations"] or "[]")
                        trans = trans_list[0] if trans_list else ""
                    except Exception:
                        trans = ""

                results.append({
                    "zolai": trans,
                    "english": row["headword"],
                    "pos": row["pos"],
                    "source": row["source"],
                    "direction": "en-zo",
                })

            return results
        except Exception as e:
            logger.debug("EN→ZO search failed: %s", e)
            return []
        finally:
            conn.close()

    def _is_duplicate(self, zolai: str, english: str) -> bool:
        """Check if entry already exists."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT COUNT(*) FROM dictionary WHERE zolai = ? AND english_clean = ?",
                (zolai.lower(), english),
            )
            return cur.fetchone()[0] > 0
        except Exception:
            return False
        finally:
            conn.close()

    def get_entry(self, zolai: str) -> dict[str, Any] | None:
        """Get a single dictionary entry."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT * FROM dictionary WHERE zolai = ?",
                (zolai.lower(),),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.debug("Get entry failed: %s", e)
            return None
        finally:
            conn.close()

    def check_attestation(self, word: str) -> dict[str, Any]:
        """Check if a word is attested in Bible or dictionary.

        Returns:
            Dict with 'attested', 'sources', 'count'.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        sources = []

        # Check dictionary
        try:
            cur.execute(
                "SELECT COUNT(*) FROM dictionary WHERE zolai = ?",
                (word.lower(),),
            )
            if cur.fetchone()[0] > 0:
                sources.append("dictionary")
        except Exception:
            pass

        # Check Bible
        try:
            cur.execute(
                "SELECT COUNT(*) FROM bible_verses WHERE zolai_text LIKE ?",
                (f"%{word}%",),
            )
            if cur.fetchone()[0] > 0:
                sources.append("bible")
        except Exception:
            pass

        # Check vocab
        try:
            cur.execute(
                "SELECT COUNT(*) FROM vocab WHERE word = ?",
                (word.lower(),),
            )
            if cur.fetchone()[0] > 0:
                sources.append("vocab")
        except Exception:
            pass

        return {
            "word": word,
            "attested": len(sources) > 0,
            "sources": sources,
            "count": len(sources),
        }
