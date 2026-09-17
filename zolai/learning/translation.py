"""EN↔ZO translation with dictionary-first lookup."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class TranslationEngine:
    """Translate between English and Zolai.

    Uses dictionary-first lookup, phrase matching, and stores corrections.
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def translate(
        self,
        text: str,
        direction: str = "auto",
        context: str | None = None,
    ) -> dict[str, Any]:
        """Translate text.

        Args:
            text: Text to translate.
            direction: "zo-en", "en-zo", or "auto".
            context: Optional context (e.g., "bible", "daily").

        Returns:
            Dict with translation, confidence, sources.
        """
        text = text.strip()
        if not text:
            return {"translation": "", "confidence": 0, "sources": []}

        # Auto-detect direction
        if direction == "auto":
            direction = self._detect_direction(text)

        # Try dictionary lookup first
        result = self._dictionary_lookup(text, direction)
        if result and result["confidence"] > 0.8:
            return result

        # Try phrase matching
        phrase_result = self._phrase_match(text, direction)
        if phrase_result and phrase_result["confidence"] > 0.7:
            return phrase_result

        # Try Bible search for context
        bible_result = self._bible_search(text, direction)
        if bible_result and bible_result["confidence"] > 0.6:
            return bible_result

        # Return best result or empty
        return result or phrase_result or bible_result or {
            "translation": "",
            "confidence": 0,
            "sources": [],
            "note": "No translation found",
        }

    def _detect_direction(self, text: str) -> str:
        """Auto-detect translation direction."""
        # Simple heuristic: if mostly ASCII and common English words, it's EN→ZO
        english_words = {"the", "a", "an", "is", "are", "was", "were", "have", "has", "had"}
        words = set(text.lower().split())

        if words & english_words:
            return "en-zo"
        return "zo-en"

    def _dictionary_lookup(
        self,
        text: str,
        direction: str,
    ) -> dict[str, Any] | None:
        """Lookup in dictionary tables."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            if direction == "zo-en":
                cur.execute(
                    """SELECT zolai, english_clean, english, pos, source
                       FROM dictionary
                       WHERE zolai = ? LIMIT 1""",
                    (text.lower(),),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "translation": row["english_clean"] or row["english"],
                        "confidence": 0.95,
                        "sources": ["dictionary"],
                        "zolai": row["zolai"],
                        "english": row["english_clean"] or row["english"],
                        "pos": row["pos"],
                    }
            else:
                cur.execute(
                    """SELECT headword, translations_clean, translations, pos, source
                       FROM dictionary_en_zo
                       WHERE headword = ? LIMIT 1""",
                    (text.lower(),),
                )
                row = cur.fetchone()
                if row:
                    import json
                    trans = row["translations_clean"]
                    if not trans:
                        try:
                            trans_list = json.loads(row["translations"] or "[]")
                            trans = trans_list[0] if trans_list else ""
                        except Exception:
                            trans = ""

                    return {
                        "translation": trans,
                        "confidence": 0.95,
                        "sources": ["dictionary"],
                        "zolai": trans,
                        "english": row["headword"],
                        "pos": row["pos"],
                    }

            return None
        except Exception as e:
            logger.debug("Dictionary lookup failed: %s", e)
            return None
        finally:
            conn.close()

    def _phrase_match(
        self,
        text: str,
        direction: str,
    ) -> dict[str, Any] | None:
        """Match multi-word phrases."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT zolai, english, category
                   FROM phrases
                   WHERE zolai LIKE ? OR english LIKE ?
                   LIMIT 5""",
                (f"%{text}%", f"%{text}%"),
            )
            rows = cur.fetchall()

            if rows:
                # Return best match
                best = rows[0]
                return {
                    "translation": best["english"] if direction == "zo-en" else best["zolai"],
                    "confidence": 0.85,
                    "sources": ["phrases"],
                    "category": best["category"],
                }

            return None
        except Exception as e:
            logger.debug("Phrase match failed: %s", e)
            return None
        finally:
            conn.close()

    def _bible_search(
        self,
        text: str,
        direction: str,
    ) -> dict[str, Any] | None:
        """Search Bible for context."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            if direction == "zo-en":
                cur.execute(
                    """SELECT zolai_text, english_text, book, chapter, verse
                       FROM bible_verses
                       WHERE zolai_text LIKE ?
                       LIMIT 3""",
                    (f"%{text}%",),
                )
            else:
                cur.execute(
                    """SELECT zolai_text, english_text, book, chapter, verse
                       FROM bible_verses
                       WHERE english_text LIKE ?
                       LIMIT 3""",
                    (f"%{text}%",),
                )

            rows = cur.fetchall()
            if rows:
                # Extract translation from verse
                translations = []
                for row in rows:
                    if direction == "zo-en":
                        translations.append(row["english_text"])
                    else:
                        translations.append(row["zolai_text"])

                return {
                    "translation": translations[0] if translations else "",
                    "confidence": 0.7,
                    "sources": ["bible"],
                    "verses": [
                        {
                            "book": r["book"],
                            "chapter": r["chapter"],
                            "verse": r["verse"],
                        }
                        for r in rows
                    ],
                }

            return None
        except Exception as e:
            logger.debug("Bible search failed: %s", e)
            return None
        finally:
            conn.close()

    def store_correction(
        self,
        original: str,
        corrected: str,
        direction: str,
        user_id: str = "anonymous",
    ) -> dict[str, Any]:
        """Store a user correction for future improvement.

        Args:
            original: Original text.
            corrected: Corrected translation.
            direction: Translation direction.
            user_id: User who made the correction.

        Returns:
            Dict with success status.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Log to audit
            cur.execute(
                """INSERT INTO data_audit_log
                   (table_name, row_id, field, old_value, new_value, changed_at, reason)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
                (
                    "translations",
                    0,
                    "correction",
                    original,
                    corrected,
                    f"Correction by {user_id}: {direction}",
                ),
            )

            conn.commit()
            return {
                "success": True,
                "original": original,
                "corrected": corrected,
                "direction": direction,
            }

        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()


    def translate_batch(
        self,
        texts: list[str],
    ) -> list[dict[str, Any]]:
        """Translate multiple texts in batch.

        Args:
            texts: List of texts to translate.

        Returns:
            List of translation results.
        """
        results = []
        for text in texts:
            result = self.translate(text)
            results.append({
                "input": text,
                "result": result,
            })
        return results

    def get_translation_stats(self) -> dict[str, Any]:
        """Get translation statistics.

        Returns:
            Dict with translation statistics.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Count translations
            cur.execute("SELECT COUNT(*) FROM translations")
            total_translations = cur.fetchone()[0]

            # Count corrections
            cur.execute("""
                SELECT COUNT(*) FROM data_audit_log 
                WHERE table_name = 'translations' AND field = 'correction'
            """)
            total_corrections = cur.fetchone()[0]

            # Count dictionary entries
            cur.execute("SELECT COUNT(*) FROM dictionary")
            dictionary_entries = cur.fetchone()[0]

            # Count Bible verses
            cur.execute("SELECT COUNT(*) FROM bible_verses")
            bible_verses = cur.fetchone()[0]

            return {
                "total_translations": total_translations,
                "total_corrections": total_corrections,
                "dictionary_entries": dictionary_entries,
                "bible_verses": bible_verses,
                "correction_rate": (
                    total_corrections / total_translations 
                    if total_translations > 0 else 0
                ),
            }

        except Exception as e:
            logger.debug("Get translation stats failed: %s", e)
            return {
                "total_translations": 0,
                "total_corrections": 0,
                "dictionary_entries": 0,
                "bible_verses": 0,
                "correction_rate": 0,
            }
        finally:
            conn.close()
