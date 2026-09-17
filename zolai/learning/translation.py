"""EN↔ZO translation with dictionary-first lookup, 3-tier confidence, and morphology awareness."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


# ── Evidence tier confidence scores ──────────────────────────────────────────
_TIER_CONFIDENCE: dict[str, float] = {
    "dictionary": 0.95,
    "bible": 0.85,
    "corpus": 0.70,
    "alignment": 0.80,
    "phrase": 0.88,
    "morphology": 0.60,
}


class TranslationEngine:
    """Translate between English and Zolai.

    Uses dictionary-first lookup, phrase matching, word alignment,
    and stores corrections. Returns 3-tier confidence scores with
    evidence sources and morphology-aware translation for unknown words.
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
        """Translate text with 3-tier confidence scoring.

        Args:
            text: Text to translate.
            direction: "zo-en", "en-zo", or "auto".
            context: Optional context (e.g., "bible", "daily").

        Returns:
            Dict with translation, confidence, sources, tier, evidence_chain.
        """
        text = text.strip()
        if not text:
            return {"translation": "", "confidence": 0, "sources": [], "tier": "none", "evidence_chain": []}

        # Auto-detect direction
        if direction == "auto":
            direction = self._detect_direction(text)

        evidence_chain: list[dict[str, Any]] = []

        # Try dictionary lookup first (tier 1: highest confidence)
        result = self._dictionary_lookup(text, direction)
        if result and result["confidence"] > 0.8:
            # Check for polysemy: if word has multiple meanings, disambiguate
            disambig = self._disambiguate_polysemy(text, context=context, direction=direction)
            if disambig.get("total_senses", 0) and disambig["total_senses"] > 1:
                # Use the top candidate from disambiguation
                top = disambig["candidates"][0]
                result["translation"] = top["meaning"]
                result["confidence"] = top["confidence"]
                result["disambiguation"] = {
                    "total_senses": disambig["total_senses"],
                    "candidates": disambig["candidates"],
                    "selected": 0,
                }

            tier = self._get_evidence_tier("dictionary")
            result["tier"] = tier
            result["confidence"] = self._compute_confidence(tier, [result])
            result["evidence_chain"] = [{"source": "dictionary", "confidence": result["confidence"]}]
            return result

        # Try phrase matching (tier 1.5: high confidence)
        phrase_result = self._phrase_match_enhanced(text, direction)
        if phrase_result and phrase_result["confidence"] > 0.7:
            tier = self._get_evidence_tier("phrase")
            phrase_result["tier"] = tier
            phrase_result["confidence"] = self._compute_confidence(tier, [phrase_result])
            phrase_result["evidence_chain"] = [{"source": "phrases", "confidence": phrase_result["confidence"]}]
            evidence_chain.append({"source": "phrases", "confidence": phrase_result["confidence"]})
            return phrase_result

        # Try word alignment (tier 2: bible evidence)
        alignment_result = self._lookup_word_alignment(text, direction)
        if alignment_result and alignment_result["confidence"] > 0.6:
            tier = self._get_evidence_tier("alignment")
            alignment_result["tier"] = tier
            alignment_result["confidence"] = self._compute_confidence(tier, [alignment_result])
            alignment_result["evidence_chain"] = [
                {"source": "word_alignment", "confidence": alignment_result["confidence"]}
            ]
            evidence_chain.append({"source": "word_alignment", "confidence": alignment_result["confidence"]})
            return alignment_result

        # Try Bible search for context (tier 2: bible evidence)
        bible_result = self._bible_search(text, direction)
        if bible_result and bible_result["confidence"] > 0.6:
            tier = self._get_evidence_tier("bible")
            bible_result["tier"] = tier
            bible_result["confidence"] = self._compute_confidence(tier, [bible_result])
            bible_result["evidence_chain"] = [{"source": "bible", "confidence": bible_result["confidence"]}]
            evidence_chain.append({"source": "bible", "confidence": bible_result["confidence"]})
            return bible_result

        # Try morphology-aware translation for unknown words (tier 3: corpus)
        morph_result = self.translate_morphology_aware(text, direction)
        if morph_result and morph_result["confidence"] > 0.3:
            tier = self._get_evidence_tier("corpus")
            morph_result["tier"] = tier
            morph_result["confidence"] = self._compute_confidence(tier, [morph_result])
            morph_result["evidence_chain"] = [{"source": "morphology", "confidence": morph_result["confidence"]}]
            return morph_result

        # Return best result or empty
        best = result or phrase_result or alignment_result or bible_result or morph_result
        if best:
            best["tier"] = best.get("tier", "none")
            best["evidence_chain"] = best.get("evidence_chain", [])
            return best

        return {
            "translation": "",
            "confidence": 0,
            "sources": [],
            "tier": "none",
            "note": "No translation found",
            "evidence_chain": [],
        }

    # ── Polysemy disambiguation ──────────────────────────────────────────

    def _disambiguate_polysemy(
        self,
        word: str,
        context: str | None = None,
        direction: str = "zo-en",
    ) -> dict[str, Any]:
        """Disambiguate polysemous words using dictionary + word_usage context.

        When a word has multiple meanings, this method:
        1. Fetches all meanings from the dictionary
        2. If context is provided, queries word_usage for per-book frequency
        3. Ranks meanings by context relevance + frequency
        4. Returns candidates with confidence and evidence

        Args:
            word: The polysemous word.
            context: Optional context string (e.g., "bible", book name, or sentence).
            direction: Translation direction.

        Returns:
            Dict with candidates list, each having meaning, confidence, evidence.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Fetch all dictionary entries for this word
            if direction == "zo-en":
                cur.execute(
                    """SELECT zolai, english_clean, english, pos, source
                       FROM dictionary
                       WHERE zolai = ?""",
                    (word.lower(),),
                )
            else:
                cur.execute(
                    """SELECT headword, translations_clean, translations, pos, source
                       FROM dictionary_en_zo
                       WHERE headword = ?""",
                    (word.lower(),),
                )

            rows = cur.fetchall()

            if not rows:
                return {"candidates": [], "word": word, "direction": direction}

            if len(rows) == 1:
                # Single meaning — no disambiguation needed
                row = rows[0]
                if direction == "zo-en":
                    meaning = row["english_clean"] or row["english"]
                else:
                    import json
                    trans = row["translations_clean"]
                    if not trans:
                        try:
                            trans_list = json.loads(row["translations"] or "[]")
                            trans = trans_list[0] if trans_list else ""
                        except Exception:
                            trans = ""
                    meaning = trans

                return {
                    "candidates": [{
                        "meaning": meaning,
                        "confidence": 1.0,
                        "evidence": "single dictionary entry",
                    }],
                    "word": word,
                    "direction": direction,
                }

            # Multiple meanings — disambiguate
            candidates: list[dict[str, Any]] = []
            book_frequencies: dict[str, int] = {}

            # Query word_usage for per-book frequency if context is available
            if context:
                try:
                    cur.execute(
                        """SELECT book, frequency
                           FROM word_usage
                           WHERE word = ?
                           ORDER BY frequency DESC""",
                        (word.lower(),),
                    )
                    for urow in cur.fetchall():
                        book_frequencies[urow["book"]] = urow["frequency"]
                except Exception:
                    pass  # word_usage table may not have this word

            # Build candidates from all dictionary entries
            for i, row in enumerate(rows):
                if direction == "zo-en":
                    meaning = row["english_clean"] or row["english"]
                    pos = row["pos"] or ""
                else:
                    import json
                    trans = row["translations_clean"]
                    if not trans:
                        try:
                            trans_list = json.loads(row["translations"] or "[]")
                            trans = trans_list[0] if trans_list else ""
                        except Exception:
                            trans = ""
                    meaning = trans
                    pos = row["pos"] or ""

                # Base confidence: first entry slightly higher (ordering bias)
                confidence = 0.5 + 0.1 * (1.0 / (i + 1))

                # Context boost: if context mentions a book where this meaning is common
                evidence_parts = [f"dictionary entry {i + 1}/{len(rows)}"]

                if context and book_frequencies:
                    # Simple heuristic: boost if context word appears in book names
                    context_lower = context.lower()
                    for book, freq in book_frequencies.items():
                        if context_lower in book.lower() or book.lower() in context_lower:
                            boost = min(0.3, freq / 1000.0)
                            confidence += boost
                            evidence_parts.append(f"context match: {book} (freq={freq})")

                # POS diversity bonus: different POS entries are more likely
                # to be genuinely different senses
                if i > 0 and pos:
                    prev_pos = rows[i - 1]["pos"] if rows[i - 1]["pos"] else ""
                    if pos != prev_pos:
                        confidence += 0.05
                        evidence_parts.append(f"POS '{pos}' differs from previous")

                confidence = min(1.0, max(0.1, confidence))

                candidates.append({
                    "meaning": meaning,
                    "confidence": round(confidence, 3),
                    "evidence": "; ".join(evidence_parts),
                    "pos": pos,
                })

            # Sort by confidence descending
            candidates.sort(key=lambda x: x["confidence"], reverse=True)

            return {
                "candidates": candidates,
                "word": word,
                "direction": direction,
                "total_senses": len(candidates),
            }

        except Exception as e:
            logger.debug("Polysemy disambiguation failed: %s", e)
            return {"candidates": [], "word": word, "direction": direction, "error": str(e)}
        finally:
            conn.close()

    # ── Evidence tier helpers ──────────────────────────────────────────────
    def _get_evidence_tier(self, source: str) -> str:
        """Map source to evidence tier.

        Args:
            source: Source type ("dictionary", "bible", "corpus", etc.)

        Returns:
            Tier label.
        """
        tier_map = {
            "dictionary": "dictionary",
            "bible": "bible",
            "corpus": "corpus",
            "alignment": "bible",
            "phrase": "dictionary",
            "morphology": "corpus",
        }
        return tier_map.get(source, "corpus")

    def _compute_confidence(self, tier: str, matches: list[dict[str, Any]]) -> float:
        """Compute confidence score based on evidence tier and match quality.

        Args:
            tier: Evidence tier label.
            matches: List of match results.

        Returns:
            Confidence score (0.0 - 1.0).
        """
        base = _TIER_CONFIDENCE.get(tier, 0.5)

        # Adjust based on number of supporting matches
        if len(matches) > 1:
            base = min(1.0, base + 0.05 * (len(matches) - 1))

        return round(base, 3)

    # ── Word alignment lookup ─────────────────────────────────────────────
    def _lookup_word_alignment(
        self,
        word: str,
        direction: str,
    ) -> dict[str, Any] | None:
        """Lookup word-level alignment from word_alignments table.

        Uses the 385K+ word alignment records for precise word-level translation.

        Args:
            word: Single word to look up.
            direction: Translation direction.

        Returns:
            Dict with translation, confidence, sources, or None.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            if direction == "zo-en":
                cur.execute(
                    """SELECT zolai_word, english_word
                       FROM word_alignments
                       WHERE zolai_word = ?
                       ORDER BY id DESC LIMIT 3""",
                    (word.lower(),),
                )
            else:
                cur.execute(
                    """SELECT zolai_word, english_word
                       FROM word_alignments
                       WHERE english_word = ?
                       ORDER BY id DESC LIMIT 3""",
                    (word.lower(),),
                )

            rows = cur.fetchall()
            if rows:
                best = rows[0]
                return {
                    "translation": best["english_word"],
                    "confidence": 0.80,
                    "sources": ["word_alignment"],
                    "zolai": best["zolai_word"] if direction == "zo-en" else best["english_word"],
                    "english": best["english_word"] if direction == "zo-en" else best["zolai_word"],
                }

            return None
        except Exception as e:
            logger.debug("Word alignment lookup failed: %s", e)
            return None
        finally:
            conn.close()

    # ── Enhanced phrase matching ──────────────────────────────────────────
    def _phrase_match_enhanced(
        self,
        text: str,
        direction: str,
    ) -> dict[str, Any] | None:
        """Enhanced phrase matching with exact match priority and partial fallback.

        Args:
            text: Text to match.
            direction: Translation direction.

        Returns:
            Dict with translation, confidence, sources, or None.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Try exact match first
            if direction == "zo-en":
                cur.execute(
                    """SELECT zo, english, frequency
                       FROM phrases
                       WHERE zo = ?
                       LIMIT 1""",
                    (text.lower(),),
                )
            else:
                cur.execute(
                    """SELECT zo, english, frequency
                       FROM phrases
                       WHERE english = ?
                       LIMIT 1""",
                    (text.lower(),),
                )

            row = cur.fetchone()
            if row:
                return {
                    "translation": row["english"] if direction == "zo-en" else row["zo"],
                    "confidence": 0.88,
                    "sources": ["phrases"],
                    "frequency": row["frequency"],
                }

            # Fallback: partial match
            cur.execute(
                """SELECT zo, english, frequency
                   FROM phrases
                   WHERE zo LIKE ? OR english LIKE ?
                   LIMIT 5""",
                (f"%{text}%", f"%{text}%"),
            )
            rows = cur.fetchall()

            if rows:
                best = rows[0]
                return {
                    "translation": best["english"] if direction == "zo-en" else best["zo"],
                    "confidence": 0.75,
                    "sources": ["phrases_partial"],
                    "frequency": best["frequency"],
                }

            return None
        except Exception as e:
            logger.debug("Enhanced phrase match failed: %s", e)
            return None
        finally:
            conn.close()

    # ── Morphology-aware translation ──────────────────────────────────────
    def translate_morphology_aware(
        self,
        word: str,
        direction: str = "zo-en",
    ) -> dict[str, Any] | None:
        """Translate unknown words by decomposing into known morphemes.

        Uses enhanced morphology analyzer to break down agglutinated words
        and compose translations from known root meanings.

        Args:
            word: Unknown word to decompose.
            direction: Translation direction.

        Returns:
            Dict with partial translation, confidence, morphology breakdown.
        """
        try:
            from ..foundation.morphology import get_enhanced_morphology
            morph = get_enhanced_morphology()
            analysis = morph.decompose(word)

            if not analysis.segments:
                return None

            # Try to compose translation from morpheme meanings
            from zolai.morphology import _KNOWN_ROOTS
            parts_meaning: list[str] = []
            for seg in analysis.segments:
                if seg in _KNOWN_ROOTS:
                    parts_meaning.append(_KNOWN_ROOTS[seg].get("meaning", seg))
                else:
                    parts_meaning.append(seg)

            if parts_meaning:
                composed = " + ".join(parts_meaning)
                return {
                    "translation": composed,
                    "confidence": 0.55,
                    "sources": ["morphology"],
                    "morphology_breakdown": list(analysis.segments),
                    "is_partial": True,
                    "directional": analysis.directional,
                    "aspect": analysis.aspect,
                    "particle": analysis.particle,
                }

            return None
        except Exception as e:
            logger.debug("Morphology-aware translation failed: %s", e)
            return None

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
                """SELECT zo, english, frequency
                   FROM phrases
                   WHERE zo LIKE ? OR english LIKE ?
                   LIMIT 5""",
                (f"%{text}%", f"%{text}%"),
            )
            rows = cur.fetchall()

            if rows:
                # Return best match
                best = rows[0]
                return {
                    "translation": best["english"] if direction == "zo-en" else best["zo"],
                    "confidence": 0.85,
                    "sources": ["phrases"],
                    "frequency": best["frequency"],
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
                    """SELECT zo_tdb77, en_kJV, book, chapter, verse
                       FROM bible_verses
                       WHERE zo_tdb77 LIKE ?
                       LIMIT 3""",
                    (f"%{text}%",),
                )
            else:
                cur.execute(
                    """SELECT zo_tdb77, en_kJV, book, chapter, verse
                       FROM bible_verses
                       WHERE en_kJV LIKE ?
                       LIMIT 3""",
                    (f"%{text}%",),
                )

            rows = cur.fetchall()
            if rows:
                # Extract translation from verse
                translations = []
                for row in rows:
                    if direction == "zo-en":
                        translations.append(row["en_kJV"])
                    else:
                        translations.append(row["zo_tdb77"])

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
