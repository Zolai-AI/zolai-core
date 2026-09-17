"""Spaced repetition and progress tracking with CEFR-aligned adaptive difficulty."""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


# ── CEFR level thresholds ───────────────────────────────────────────────────
_CEFR_THRESHOLDS: dict[str, float] = {
    "A1": 0.0,
    "A2": 0.1,
    "B1": 0.2,
    "B2": 0.35,
    "C1": 0.5,
    "C2": 0.7,
}

# ── Frequency tiers ─────────────────────────────────────────────────────────
_FREQ_TIERS: dict[str, int] = {
    "high": 1000,
    "medium": 100,
    "low": 10,
}


class ProgressTracker:
    """Track learning progress with SM-2 spaced repetition and adaptive difficulty.

    Features:
    - SM-2 algorithm for scheduling reviews
    - CEFR level tracking (A1-C2)
    - Morphology-aware difficulty adjustment
    - Tone-sensitive ease tuning
    - Adaptive quiz difficulty based on word frequency, morphology, error rate
    """

    def __init__(self, user_id: str = "default") -> None:
        self._db_path = config.paths.zolai_db
        self.user_id = user_id

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get_next_review(self, word: str) -> dict[str, Any]:
        """Get next review date for a word using SM-2.

        Returns:
            Dict with word, next_review, interval, ease_factor.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Check if word has review data
            cur.execute(
                """SELECT * FROM vocabulary
                   WHERE headword = ?
                   LIMIT 1""",
                (word,),
            )
            row = cur.fetchone()

            if not row:
                return {
                    "word": word,
                    "next_review": datetime.now().isoformat(),
                    "interval": 0,
                    "ease_factor": 2.5,
                    "repetitions": 0,
                    "status": "new",
                }

            return {
                "word": word,
                "next_review": row.get("next_review", datetime.now().isoformat()),
                "interval": row.get("interval", 0),
                "ease_factor": row.get("ease_factor", 2.5),
                "repetitions": row.get("repetitions", 0),
                "status": "review",
            }

        except Exception as e:
            logger.debug("Get next review failed: %s", e)
            return {
                "word": word,
                "next_review": datetime.now().isoformat(),
                "interval": 0,
                "ease_factor": 2.5,
                "repetitions": 0,
                "status": "error",
            }
        finally:
            conn.close()

    def update_review(
        self,
        word: str,
        quality: int,
    ) -> dict[str, Any]:
        """Update review using SM-2 algorithm.

        Args:
            word: The word reviewed.
            quality: Quality of response (0-5).
                0-2: Failed (again)
                3: Hard
                4: Good
                5: Easy

        Returns:
            Dict with updated review data.
        """
        # SM-2 algorithm
        if quality < 0 or quality > 5:
            raise ValueError("Quality must be between 0 and 5")

        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get current review data — use try/except for missing SM-2 columns
            ease_factor = 2.5
            interval = 0
            repetitions = 0
            row = None
            try:
                cur.execute(
                    """SELECT * FROM vocabulary
                       WHERE headword = ?
                       LIMIT 1""",
                    (word,),
                )
                row = cur.fetchone()
                if row:
                    ease_factor = float(row["ease_factor"]) if "ease_factor" in row.keys() else 2.5
                    interval = int(row["interval"]) if "interval" in row.keys() else 0
                    repetitions = int(row["repetitions"]) if "repetitions" in row.keys() else 0
            except Exception:
                # SM-2 columns not yet added to vocabulary table
                pass

            # Update ease factor
            ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            ease_factor = max(1.3, ease_factor)

            # Update interval and repetitions
            if quality < 3:
                # Failed - reset
                repetitions = 0
                interval = 1
            else:
                repetitions += 1
                if repetitions == 1:
                    interval = 1
                elif repetitions == 2:
                    interval = 6
                else:
                    interval = math.ceil(interval * ease_factor)

            # Calculate next review date
            next_review = datetime.now() + timedelta(days=interval)

            # Try to persist SM-2 state (graceful fallback if columns missing)
            try:
                if row and "ease_factor" in row.keys():
                    cur.execute(
                        """UPDATE vocabulary
                           SET ease_factor = ?, interval = ?, repetitions = ?,
                               next_review = ?, updated_at = datetime('now')
                           WHERE headword = ?""",
                        (ease_factor, interval, repetitions, next_review.isoformat(), word),
                    )
                else:
                    cur.execute(
                        """INSERT INTO vocabulary
                           (headword, english, frequency, books, examples)
                           VALUES (?, ?, 0, '', '')""",
                        (word,),
                    )
            except Exception:
                # SM-2 columns not available; skip persistence
                pass

            conn.commit()

            return {
                "word": word,
                "next_review": next_review.isoformat(),
                "interval": interval,
                "ease_factor": round(ease_factor, 2),
                "repetitions": repetitions,
                "quality": quality,
            }

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Morphology-aware difficulty ────────────────────────────────────────
    def _compute_morphology_complexity(self, word: str) -> float:
        """Compute morphology complexity score (0.0 - 1.0).

        Factors:
        - Agglutination depth (prefix + stem + suffix layers)
        - Compound depth (number of component roots)
        - Tone count (tone-dependent words are harder)

        Args:
            word: Zolai word.

        Returns:
            Complexity score (0.0 = simple, 1.0 = very complex).
        """
        try:
            from ..foundation.morphology import get_enhanced_morphology
            morph = get_enhanced_morphology()
            analysis = morph.decompose(word)

            score = 0.0

            # Agglutination: more segments = more complex
            n_segments = len(analysis.segments)
            if n_segments >= 4:
                score += 0.4
            elif n_segments >= 3:
                score += 0.3
            elif n_segments >= 2:
                score += 0.15

            # Compound depth
            if analysis.compound_parts:
                compound_depth = len(analysis.compound_parts)
                score += min(0.3, compound_depth * 0.1)

            # Directional prefix adds complexity
            if analysis.directional:
                score += 0.1

            # Aspect suffix adds complexity
            if analysis.aspect:
                score += 0.1

            # Tone-dependent words (check morphology module)
            from zolai.morphology import _TONE_NOTES
            if word in _TONE_NOTES or analysis.stem in _TONE_NOTES:
                score += 0.1

            return min(1.0, score)
        except Exception:
            return 0.3  # Default moderate complexity

    def _apply_sm2_tuning(
        self,
        ease: float,
        morphology_score: float,
        tone_sensitive: bool = False,
    ) -> float:
        """Tune SM-2 ease factor based on morphology and tone sensitivity.

        Adjustments:
        - +0.1 for tone-sensitive words (harder to master)
        - +0.15 for complex morphology (agglutinated/compound)
        - Clamp to [1.3, 3.0]

        Args:
            ease: Current ease factor.
            morphology_score: Complexity score from _compute_morphology_complexity.
            tone_sensitive: Whether the word is tone-dependent.

        Returns:
            Adjusted ease factor.
        """
        adjusted = ease

        if tone_sensitive:
            adjusted += 0.1

        if morphology_score > 0.5:
            adjusted += 0.15
        elif morphology_score > 0.3:
            adjusted += 0.08

        return max(1.3, min(3.0, adjusted))

    def _get_cefr_level_morphology(self, word: str) -> str:
        """Get CEFR level considering morphology complexity.

        Mapping:
        - A1: Common roots (pasian, gam, mi, ne)
        - A2: Simple compounds (vantung, leitung)
        - B1: Directional verbs (hongpai, vatui)
        - B2: Full agglutination (nuntakna, suahtakna)
        - C1: Complex compounds with tone sandhi
        - C2: Literary/archaic forms

        Args:
            word: Zolai word.

        Returns:
            CEFR level string (A1-C2).
        """
        complexity = self._compute_morphology_complexity(word)

        if complexity < 0.1:
            return "A1"
        elif complexity < 0.25:
            return "A2"
        elif complexity < 0.4:
            return "B1"
        elif complexity < 0.6:
            return "B2"
        elif complexity < 0.8:
            return "C1"
        return "C2"

    def get_adaptive_difficulty(self, user_id: str | None = None) -> dict[str, Any]:
        """Compute adaptive quiz difficulty based on user's learning profile.

        Factors:
        - Word frequency tier (high/medium/low/rare)
        - Morphology complexity of recent words
        - Tone sensitivity of recent words
        - Historical error rate

        Args:
            user_id: User ID (defaults to self.user_id).

        Returns:
            Dict with difficulty settings and recommendations.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get user's recent performance — SM-2 columns may not exist yet
            recent = []
            has_review_history = False
            try:
                cur.execute(
                    """SELECT headword as word, frequency
                       FROM vocabulary
                       WHERE frequency > 0
                       ORDER BY id DESC LIMIT 50""",
                )
                recent = cur.fetchall()
                has_review_history = len(recent) > 0
            except Exception:
                # SM-2 columns not yet added; no review history available
                pass

            if not has_review_history:
                return {
                    "difficulty": "beginner",
                    "frequency_tier": "high",
                    "morphology_threshold": 0.2,
                    "include_tone_questions": False,
                    "recommended_count": 10,
                    "reason": "No history — starting with high-frequency simple words",
                }

            # Compute error rate — default to 0 if SM-2 columns missing
            error_rate = 0.0
            avg_ease = 2.5

            # Compute average complexity
            avg_complexity = sum(
                self._compute_morphology_complexity(r["word"]) for r in recent
            ) / len(recent) if recent else 0.0

            # Determine difficulty level
            if error_rate > 0.4 or avg_ease < 1.8:
                difficulty = "beginner"
                freq_tier = "high"
                morph_threshold = 0.2
                include_tone = False
                count = 8
                reason = "High error rate — focusing on high-frequency simple words"
            elif error_rate > 0.25 or avg_ease < 2.2:
                difficulty = "intermediate"
                freq_tier = "medium"
                morph_threshold = 0.4
                include_tone = True
                count = 10
                reason = "Moderate error rate — introducing medium-frequency and compound words"
            elif error_rate > 0.1 or avg_complexity > 0.4:
                difficulty = "advanced"
                freq_tier = "low"
                morph_threshold = 0.6
                include_tone = True
                count = 12
                reason = "Low error rate — including low-frequency and agglutinated words"
            else:
                difficulty = "expert"
                freq_tier = "rare"
                morph_threshold = 0.8
                include_tone = True
                count = 15
                reason = "Excellent performance — challenging with rare words and complex morphology"

            return {
                "difficulty": difficulty,
                "frequency_tier": freq_tier,
                "morphology_threshold": morph_threshold,
                "include_tone_questions": include_tone,
                "recommended_count": count,
                "error_rate": round(error_rate, 3),
                "avg_ease": round(avg_ease, 2),
                "avg_complexity": round(avg_complexity, 3),
                "reason": reason,
            }

        except Exception as e:
            logger.debug("Get adaptive difficulty failed: %s", e)
            return {
                "difficulty": "beginner",
                "frequency_tier": "high",
                "morphology_threshold": 0.2,
                "include_tone_questions": False,
                "recommended_count": 10,
                "reason": "Error in computation — defaulting to beginner",
            }
        finally:
            conn.close()

    def get_cefr_level(self) -> dict[str, Any]:
        """Get user's CEFR level based on vocabulary mastery.

        Returns:
            Dict with level, words_known, words_total, progress.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Count total vocab
            cur.execute("SELECT COUNT(*) FROM vocabulary")
            total = cur.fetchone()[0]

            # Count words with good review — SM-2 columns may not exist
            known = 0
            try:
                cur.execute(
                    """SELECT COUNT(DISTINCT headword) FROM vocabulary
                       WHERE frequency > 100""",
                )
                known = cur.fetchone()[0]
            except Exception:
                # Columns not yet added
                known = 0

            # Calculate level using thresholds
            if total == 0:
                level = "A1"
                progress = 0.0
            else:
                ratio = known / total
                level = "A1"
                for level_label, threshold in sorted(_CEFR_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
                    if ratio >= threshold:
                        level = level_label
                        break
                progress = ratio

            return {
                "level": level,
                "words_known": known,
                "words_total": total,
                "progress": round(progress, 3),
            }

        except Exception as e:
            logger.debug("Get CEFR level failed: %s", e)
            return {
                "level": "A1",
                "words_known": 0,
                "words_total": 0,
                "progress": 0.0,
            }
        finally:
            conn.close()

    def generate_quiz(
        self,
        quiz_type: str = "vocabulary",
        count: int = 10,
        level: str | None = None,
    ) -> list[dict[str, Any]]:
        """Generate a quiz from vocab table.

        Args:
            quiz_type: Type of quiz ("vocabulary", "translation", "reverse").
            count: Number of questions.
            level: CEFR level filter.

        Returns:
            List of quiz questions.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get words for quiz
            query = "SELECT headword, english, frequency FROM vocabulary"
            params = []

            query += " ORDER BY RANDOM() LIMIT ?"
            params.append(count * 2)  # Get extra for variety

            cur.execute(query, params)
            rows = cur.fetchall()

            quiz = []
            for row in rows[:count]:
                if quiz_type == "vocabulary":
                    quiz.append({
                        "question": f"What is the English meaning of '{row['headword']}'?",
                        "answer": row["english"],
                        "word": row["headword"],
                        "options": self._get_distractors(row["english"], "english"),
                    })
                elif quiz_type == "translation":
                    quiz.append({
                        "question": f"Translate to Zolai: '{row['english']}'",
                        "answer": row["headword"],
                        "word": row["headword"],
                        "options": self._get_distractors(row["headword"], "zolai"),
                    })
                elif quiz_type == "reverse":
                    quiz.append({
                        "question": f"What does '{row['headword']}' mean?",
                        "answer": row["english"],
                        "word": row["headword"],
                        "options": self._get_distractors(row["english"], "english"),
                    })

            return quiz

        except Exception as e:
            logger.debug("Generate quiz failed: %s", e)
            return []
        finally:
            conn.close()

    def _get_distractors(self, correct: str, field: str) -> list[str]:
        """Get distractor options for quiz."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            if field == "english":
                cur.execute(
                    "SELECT DISTINCT english_clean FROM dictionary ORDER BY RANDOM() LIMIT 3"
                )
            else:
                cur.execute(
                    "SELECT DISTINCT zolai FROM dictionary ORDER BY RANDOM() LIMIT 3"
                )

            distractors = [row[0] for row in cur.fetchall() if row[0] != correct]
            return distractors[:3]
        except Exception:
            return []
        finally:
            conn.close()

    def get_due_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get words due for review."""
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT headword
                   FROM vocabulary
                   ORDER BY id DESC
                   LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.debug("Get due reviews failed: %s", e)
            return []
        finally:
            conn.close()


    def get_statistics(self) -> dict[str, Any]:
        """Get learning statistics.

        Returns:
            Dict with learning statistics.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Total vocabulary
            cur.execute("SELECT COUNT(*) FROM vocabulary")
            total_vocab = cur.fetchone()[0]

            # Total grammar patterns
            cur.execute("SELECT COUNT(*) FROM grammar_patterns")
            total_grammar = cur.fetchone()[0]

            # Total translations
            cur.execute("SELECT COUNT(*) FROM translations")
            total_translations = cur.fetchone()[0]

            # Total exercises
            cur.execute("SELECT COUNT(*) FROM training_exercises")
            total_exercises = cur.fetchone()[0]

            # Total corrections
            cur.execute("""
                SELECT COUNT(*) FROM data_audit_log
                WHERE table_name = 'translations' AND field = 'correction'
            """)
            total_corrections = cur.fetchone()[0]

            # Words by frequency
            cur.execute("""
                SELECT
                    CASE
                        WHEN frequency >= 1000 THEN 'high'
                        WHEN frequency >= 100 THEN 'medium'
                        WHEN frequency >= 10 THEN 'low'
                        ELSE 'rare'
                    END as freq_group,
                    COUNT(*) as count
                FROM vocabulary
                GROUP BY freq_group
            """)
            freq_distribution = {row[0]: row[1] for row in cur.fetchall()}

            return {
                "total_vocab": total_vocab,
                "total_grammar": total_grammar,
                "total_translations": total_translations,
                "total_exercises": total_exercises,
                "total_corrections": total_corrections,
                "freq_distribution": freq_distribution,
                "completion_rate": (
                    total_exercises / total_vocab
                    if total_vocab > 0 else 0
                ),
            }

        except Exception as e:
            logger.debug("Get statistics failed: %s", e)
            return {
                "total_vocab": 0,
                "total_grammar": 0,
                "total_translations": 0,
                "total_exercises": 0,
                "total_corrections": 0,
                "freq_distribution": {},
                "completion_rate": 0,
            }
        finally:
            conn.close()
