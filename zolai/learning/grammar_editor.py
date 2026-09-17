"""Grammar rule CRUD operations and sentence structure validation.

Provides:
- CRUD for grammar_patterns table
- SOV word-order validation
- Ergative marker checking
- Negation and question pattern validation
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)

# ── Zolai grammatical markers ────────────────────────────────────────────

_ERGATIVE_MARKER = "in"
_NEGATION_MARKERS = {"kei", "lo"}
_QUESTION_MARKER = "hiam"
_DECLARATIVE_PARTICLES = {"hi", "hen", "un", "in", "vo"}
_ASPECT_MARKERS = {"ta", "zo", "khin", "lai", "ding"}
_DIRECTIONAL_PREFIXES = {"hong", "va", "khia", "lut", "kik"}


class GrammarEditor:
    """Manage grammar patterns in the database.

    Provides CRUD operations for grammar_patterns table and
    sentence structure validation (SOV, ergative, negation, questions).
    """

    def __init__(self) -> None:
        self._db_path = config.paths.zolai_db

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── CRUD operations ──────────────────────────────────────────────────

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
                f"UPDATE grammar_patterns SET {set_clause} WHERE id = ?",
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
            conditions: list[str] = []
            params: list[Any] = []

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

        errors: list[dict[str, str]] = []
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

    def validate_pattern(self, pattern: str, function: str = "") -> dict[str, Any]:
        """Validate a grammar pattern.

        Args:
            pattern: The pattern to validate (e.g., "S + O + V").
            function: Function type for context-specific validation.

        Returns:
            Dict with validation results.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check basic pattern structure
        if not pattern or not pattern.strip():
            errors.append("Pattern cannot be empty")
            return {"valid": False, "errors": errors, "warnings": warnings}

        # Check for common Zolai grammar markers
        markers: dict[str, list[str]] = {
            "sov": ["S", "O", "V"],
            "ergative": ["in", "ERG"],
            "negation": ["kei", "lo"],
            "question": ["hiam"],
            "future": ["ding"],
        }

        if function in markers:
            required = markers[function]
            found = [m for m in required if m.upper() in pattern.upper()]
            if not found:
                warnings.append(f"Pattern for {function} should contain: {', '.join(required)}")

        # Check for ZVS compliance in any Zolai text
        zvs_result = self._validate_zvs(pattern)
        if not zvs_result["is_compliant"]:
            errors.extend([
                f"ZVS violation: {e['forbidden']} → {e['correct']}"
                for e in zvs_result["errors"]
            ])

        # Check pattern length
        if len(pattern) > 500:
            warnings.append("Pattern is very long, consider simplifying")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "corrected_text": zvs_result.get("corrected_text", pattern),
        }

    # ── Sentence structure validation ────────────────────────────────────

    @staticmethod
    def _tokenize_sentence(sentence: str) -> list[str]:
        """Tokenize a Zolai sentence into words.

        Strips punctuation and splits on whitespace.

        Args:
            sentence: Raw sentence text.

        Returns:
            List of word tokens.
        """
        # Remove trailing punctuation (periods, question marks)
        cleaned = re.sub(r"[.?!]+$", "", sentence.strip())
        # Split on whitespace
        return [t for t in cleaned.split() if t]

    @staticmethod
    def _is_particle(token: str) -> bool:
        """Check if a token is a grammatical particle.

        Particles include: hi, hen, un, in (non-ergative), vo, ta, zo,
        khin, lai, ding, kei, lo, hiam.

        Args:
            token: Word token.

        Returns:
            True if token is a particle.
        """
        t = token.lower()
        return t in _DECLARATIVE_PARTICLES | _ASPECT_MARKERS | _NEGATION_MARKERS | {_QUESTION_MARKER}

    @staticmethod
    def _is_ergative_in(tokens: list[str], idx: int) -> bool:
        """Check if 'in' at idx is used as ergative marker.

        Ergative 'in' appears after a noun phrase (agent) and before
        the object + verb. It is NOT ergative when used as a
        postverbal particle.

        Args:
            tokens: Full token list.
            idx: Index of the 'in' token.

        Returns:
            True if 'in' is used as ergative marker.
        """
        if idx == 0:
            return False  # Sentence-initial 'in' is not ergative
        # If 'in' appears after at least one noun and before more tokens,
        # it is likely ergative
        prev = tokens[idx - 1].lower()
        # Ergative follows a noun (not a particle)
        return prev not in _DECLARATIVE_PARTICLES

    def validate_sentence_structure(self, sentence: str) -> dict[str, Any]:
        """Validate SOV structure and grammar markers in a Zolai sentence.

        Checks:
        - Tokenization
        - Verb position (last non-particle token should be verb)
        - Ergative 'in' placement after transitive agent
        - Negation markers (kei/lo) before verb
        - Question marker (hiam) at sentence end
        - ZVS 2018 compliance

        Args:
            sentence: Zolai sentence to validate.

        Returns:
            Dict with valid (bool), errors (list), warnings (list).
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not sentence or not sentence.strip():
            return {"valid": False, "errors": ["Empty sentence"], "warnings": []}

        tokens = self._tokenize_sentence(sentence)
        if not tokens:
            return {"valid": False, "errors": ["Could not tokenize sentence"], "warnings": []}

        # ── 1. ZVS compliance ───────────────────────────────────────────
        zvs = self._validate_zvs(sentence)
        if not zvs["is_compliant"]:
            for err in zvs["errors"]:
                errors.append(f"ZVS: '{err['forbidden']}' → '{err['correct']}'")

        # ── 2. Find verb position ───────────────────────────────────────
        # Last non-particle token is the verb (or sentence-final particle)
        verb_idx: int | None = None
        for i in range(len(tokens) - 1, -1, -1):
            if not self._is_particle(tokens[i]):
                verb_idx = i
                break

        if verb_idx is None:
            warnings.append("No verb found (all tokens are particles)")
        elif verb_idx < len(tokens) - 1:
            # Verb is not last — check if particles follow (normal)
            pass
        elif verb_idx > 0:
            # Verb is last — good SOV indicator
            pass

        # ── 3. Ergative 'in' check ──────────────────────────────────────
        in_indices = [i for i, t in enumerate(tokens) if t.lower() == "in"]
        for idx in in_indices:
            if self._is_ergative_in(tokens, idx):
                # Check: ergative requires at least one token before and one after
                if idx == 0:
                    errors.append("Ergative 'in' cannot be sentence-initial")
                elif idx == len(tokens) - 1:
                    errors.append("Ergative 'in' cannot be sentence-final")

        # ── 4. Negation check ───────────────────────────────────────────
        for i, t in enumerate(tokens):
            if t.lower() in _NEGATION_MARKERS:
                # Negation should come before the verb
                if verb_idx is not None and i > verb_idx:
                    errors.append(
                        f"Negation '{t}' at position {i} appears after verb "
                        f"at position {verb_idx} — expected before verb"
                    )

        # ── 5. Question marker check ────────────────────────────────────
        for i, t in enumerate(tokens):
            if t.lower() == _QUESTION_MARKER:
                if i < len(tokens) - 1:
                    errors.append(
                        f"Question marker '{_QUESTION_MARKER}' at position {i} "
                        f"is not sentence-final"
                    )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "tokens": tokens,
            "verb_position": verb_idx,
            "zvs_compliant": zvs["is_compliant"],
        }

    def validate_text(self, text: str) -> dict[str, Any]:
        """Validate sentence structure for all sentences in a text.

        Splits text by sentence delimiters (. ! ?) and validates each.

        Args:
            text: Multi-sentence Zolai text.

        Returns:
            Dict with overall valid (bool), per-sentence results, error count.
        """
        if not text or not text.strip():
            return {
                "valid": False,
                "sentence_count": 0,
                "results": [],
                "error_count": 0,
            }

        # Split on sentence-ending punctuation
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        results = []
        total_errors = 0

        for sent in sentences:
            result = self.validate_sentence_structure(sent)
            result["sentence"] = sent
            results.append(result)
            total_errors += len(result["errors"])

        return {
            "valid": total_errors == 0,
            "sentence_count": len(sentences),
            "results": results,
            "error_count": total_errors,
        }
