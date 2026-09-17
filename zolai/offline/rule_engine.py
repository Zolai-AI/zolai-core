"""Rule-based translation and grammar engine using SQLite data."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)

# ZVS 2018 forbidden forms mapping
ZVS_CORRECTIONS = {
    "pathian": "pasian",
    "ram": "gam",
    "fapa": "tapa",
    "bawipa": "topa",
    "siangpahrang": "kumpipa",
    "cu": "tua",
    "cun": "tua",
    "suah": "suahtakna",
    "nunnak": "nuntakna",
}

# SOV patterns
SOV_PATTERN = re.compile(
    r"^(?P<subject>\w+)\s+(?P<object>\w+)\s+(?P<verb>\w+)\s*(?:hi\s*)?\.*$",
    re.IGNORECASE,
)


class RuleEngine:
    """Rule-based translation and grammar checking using SQLite data.

    Provides:
    - Dictionary lookup (ZO→EN, EN→ZO)
    - ZVS 2018 compliance checking
    - SOV word order validation
    - Basic grammar pattern matching
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def translate(self, text: str) -> str | None:
        """Translate text using dictionary lookup.

        Tries ZO→EN first, then EN→ZO.
        Returns translation or None if not found.
        """
        text = text.strip()
        if not text:
            return None

        # Try ZO→EN
        result = self._lookup_zo_en(text)
        if result:
            return result

        # Try EN→ZO
        result = self._lookup_en_zo(text)
        if result:
            return result

        # Try word-by-word translation
        words = text.split()
        translations = []
        for word in words:
            t = self._lookup_zo_en(word) or self._lookup_en_zo(word) or word
            translations.append(t)

        if any(t != w for t, w in zip(translations, words)):
            return " ".join(translations)

        return None

    def _lookup_zo_en(self, word: str) -> str | None:
        """Lookup Zolai → English."""
        try:
            conn = self._get_connection()
            cur = conn.execute(
                "SELECT english_clean, english FROM dictionary WHERE zolai = ? LIMIT 1",
                (word.lower(),),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return row["english_clean"] or row["english"]
        except Exception as e:
            logger.debug("ZO→EN lookup failed: %s", e)
        return None

    def _lookup_en_zo(self, word: str) -> str | None:
        """Lookup English → Zolai."""
        try:
            conn = self._get_connection()
            cur = conn.execute(
                "SELECT translations_clean, translations FROM dictionary_en_zo WHERE headword = ? LIMIT 1",
                (word.lower(),),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                result = row["translations_clean"]
                if result:
                    return result
                # Parse JSON array
                import json
                try:
                    trans_list = json.loads(row["translations"] or "[]")
                    return trans_list[0] if trans_list else None
                except Exception:
                    pass
        except Exception as e:
            logger.debug("EN→ZO lookup failed: %s", e)
        return None

    def check_zvs_compliance(self, text: str) -> dict[str, Any]:
        """Check ZVS 2018 compliance and suggest corrections.

        Returns:
            Dict with 'is_compliant', 'errors', 'corrected_text'.
        """
        errors = []
        corrected = text

        for forbidden, correct in ZVS_CORRECTIONS.items():
            # Case-insensitive check
            pattern = re.compile(rf"\b{forbidden}\b", re.IGNORECASE)
            if pattern.search(corrected):
                errors.append({
                    "forbidden": forbidden,
                    "correct": correct,
                    "position": pattern.search(corrected).start(),
                })
                corrected = pattern.sub(correct, corrected)

        return {
            "is_compliant": len(errors) == 0,
            "errors": errors,
            "corrected_text": corrected,
        }

    def validate_sov(self, sentence: str) -> dict[str, Any]:
        """Validate SOV word order.

        Returns:
            Dict with 'is_valid', 'pattern', 'suggestion'.
        """
        # Simple heuristic: check if verb is at the end
        words = sentence.strip().rstrip(".").split()

        if len(words) < 2:
            return {"is_valid": True, "pattern": "too_short", "suggestion": ""}

        # Common verb endings
        verb_endings = ("hi", "ta", "zo", "ding", "lai", "khin", "hen", "vo")
        last_word = words[-1].lower()

        # Check if sentence ends with verb particle
        is_sov = last_word in verb_endings or any(
            last_word.endswith(e) for e in verb_endings
        )

        return {
            "is_valid": is_sov,
            "pattern": "sov" if is_sov else "unknown",
            "suggestion": "" if is_sov else "Zolai uses SOV word order (Subject-Object-Verb).",
        }

    def search_dictionary(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search dictionary with ZO↔EN."""
        results = []

        # ZO→EN
        try:
            conn = self._get_connection()
            cur = conn.execute(
                """SELECT zolai, english_clean, english, pos, source
                   FROM dictionary
                   WHERE zolai LIKE ? OR english_clean LIKE ?
                   LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            )
            for row in cur.fetchall():
                results.append({
                    "zolai": row["zolai"],
                    "english": row["english_clean"] or row["english"],
                    "pos": row["pos"],
                    "source": row["source"],
                    "direction": "zo-en",
                })
            conn.close()
        except Exception as e:
            logger.debug("Dictionary search failed: %s", e)

        return results[:limit]

    def get_grammar_patterns(self, pattern_type: str | None = None) -> list[dict[str, Any]]:
        """Get grammar patterns from database."""
        try:
            conn = self._get_connection()
            if pattern_type:
                cur = conn.execute(
                    "SELECT * FROM grammar_patterns WHERE pattern_type = ? LIMIT 50",
                    (pattern_type,),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM grammar_patterns LIMIT 50"
                )
            results = [dict(row) for row in cur.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.debug("Grammar patterns lookup failed: %s", e)
            return []
