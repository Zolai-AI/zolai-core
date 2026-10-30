"""Dictionary CRUD operations."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..config import config
from ..shared.clock import stamp

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
            # Write both mirrors: `english` is the canonical column other
            # consumers read, `english_clean` the search copy.
            cur.execute(
                """INSERT INTO dictionary
                   (zolai, english, english_clean, myanmar, pos, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (zolai.lower(), english, english, myanmar, pos, source),
            )
            dictionary_id = cur.lastrowid

            cur.execute(
                """INSERT INTO dictionary_en_zo
                   (headword, translations, translations_clean, pos, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    english.lower(),
                    json.dumps([zolai], ensure_ascii=False),
                    zolai,
                    pos,
                    source,
                ),
            )

            # Log to audit — row_id is the ZO→EN row, not the reverse mirror.
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "dictionary",
                    dictionary_id,
                    "entry",
                    "",
                    f"{zolai}: {english}",
                    stamp(),
                    f"Added by {source}",
                ),
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

            # Only known columns are writable; the audit loop below logs
            # exactly the fields that were applied, never the request alone.
            applied: dict[str, Any] = {}
            if "english" in kwargs:
                cur.execute(
                    "UPDATE dictionary SET english = ?, english_clean = ? WHERE zolai = ?",
                    (kwargs["english"], kwargs["english"], zolai.lower()),
                )
                applied["english"] = kwargs["english"]

            if "myanmar" in kwargs:
                cur.execute(
                    "UPDATE dictionary SET myanmar = ? WHERE zolai = ?",
                    (kwargs["myanmar"], zolai.lower()),
                )
                applied["myanmar"] = kwargs["myanmar"]

            if "pos" in kwargs:
                cur.execute(
                    "UPDATE dictionary SET pos = ? WHERE zolai = ?",
                    (kwargs["pos"], zolai.lower()),
                )
                applied["pos"] = kwargs["pos"]

            # Log to audit
            for field, new_value in applied.items():
                old_value = old_values.get(field, "")
                cur.execute(
                    """INSERT INTO data_audit_log
                       (table_name, row_id, field, old_value, new_value, changed_at, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "dictionary",
                        0,
                        field,
                        str(old_value),
                        str(new_value),
                        stamp(),
                        "Updated via DictionaryManager",
                    ),
                )

            conn.commit()
            return {
                "success": True,
                "zolai": zolai,
                "updated_fields": list(applied),
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
                "UPDATE dictionary SET is_deleted = 1, deleted_at = ? WHERE zolai = ?",
                (stamp(), zolai.lower()),
            )

            # Log to audit
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("dictionary", 0, "is_deleted", "0", "1", stamp(), f"Deleted by {deleted_by}: {reason}"),
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
        except Exception as exc:  # noqa: BLE001 — duplicate check must not crash the add
            logger.debug("duplicate check failed: %s", exc)
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
            Dict with 'attested', 'sources', 'count' — plus ``errors`` naming
            any probe that could not run, so a broken probe is never mistaken
            for evidence of absence.
        """
        probes = (
            ("dictionary", "SELECT COUNT(*) FROM dictionary WHERE zolai = ?",
             (word.lower(),)),
            # Canonical column is zo_tdb77 (the old zolai_text never existed).
            ("bible", "SELECT COUNT(*) FROM bible_verses WHERE zo_tdb77 LIKE ?",
             (f"%{word}%",)),
            ("vocab", "SELECT COUNT(*) FROM vocabulary WHERE headword = ?",
             (word.lower(),)),
        )

        conn = self._get_connection()
        cur = conn.cursor()
        sources: list[str] = []
        errors: list[str] = []

        try:
            for name, sql, params in probes:
                try:
                    cur.execute(sql, params)
                    if cur.fetchone()[0] > 0:
                        sources.append(name)
                except Exception as exc:  # noqa: BLE001 — one probe must not hide the rest
                    logger.debug("attestation probe %s failed: %s", name, exc)
                    errors.append(name)
        finally:
            conn.close()

        result: dict[str, Any] = {
            "word": word,
            "attested": len(sources) > 0,
            "sources": sources,
            "count": len(sources),
        }
        if errors:
            result["errors"] = errors
        return result
