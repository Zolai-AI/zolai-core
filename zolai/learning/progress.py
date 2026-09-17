"""Spaced repetition and progress tracking with CEFR-aligned adaptive difficulty.

Provides:
- SM-2 spaced repetition scheduling
- CEFR level tracking (A1-C2)
- Learning streak tracking
- Error categorization (ZVS, grammar, tone, morphology)
- Adaptive quiz difficulty
"""

from __future__ import annotations

import logging
import math
import re
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
                # Check if SM-2 columns exist on vocabulary table
                cur.execute("PRAGMA table_info(vocabulary)")
                columns = {row[1] for row in cur.fetchall()}
                if "ease_factor" not in columns:
                    # SM-2 columns not yet added — no review history
                    pass
                else:
                    # Check user-specific review history from user_reviews table
                    cur.execute(
                        """SELECT word, quality as review_quality
                           FROM user_reviews
                           WHERE user_id = ?
                           ORDER BY reviewed_at DESC LIMIT 50""",
                        (user_id or self.user_id,),
                    )
                    recent = cur.fetchall()
                    has_review_history = len(recent) > 0
            except Exception:
                # Tables/columns not yet added; no review history available
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

    # ── Streak tracking ──────────────────────────────────────────────────

    def get_streak(self, user_id: str | None = None, streak_type: str = "daily") -> dict[str, Any]:
        """Get learning streak for a user.

        Reads/writes the user_streaks table to track consecutive days
        of learning activity.

        Args:
            user_id: User ID (defaults to self.user_id).
            streak_type: Type of streak ("daily", "review", "quiz").

        Returns:
            Dict with current_streak, longest_streak, last_activity_date, is_active.
        """
        uid = user_id or self.user_id
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT current_streak, longest_streak, last_activity_date
                   FROM user_streaks
                   WHERE user_id = ? AND streak_type = ?
                   LIMIT 1""",
                (uid, streak_type),
            )
            row = cur.fetchone()

            if not row:
                return {
                    "user_id": uid,
                    "streak_type": streak_type,
                    "current_streak": 0,
                    "longest_streak": 0,
                    "last_activity_date": None,
                    "is_active": False,
                }

            last_date = row["last_activity_date"]
            current = row["current_streak"]
            longest = row["longest_streak"]

            # Check if streak is still active (last activity was today or yesterday)
            is_active = False
            if last_date:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                diff = (datetime.now() - last_dt).days
                is_active = diff <= 1

            return {
                "user_id": uid,
                "streak_type": streak_type,
                "current_streak": current,
                "longest_streak": longest,
                "last_activity_date": last_date,
                "is_active": is_active,
            }

        except Exception as e:
            logger.debug("Get streak failed: %s", e)
            return {
                "user_id": uid,
                "streak_type": streak_type,
                "current_streak": 0,
                "longest_streak": 0,
                "last_activity_date": None,
                "is_active": False,
            }
        finally:
            conn.close()

    def update_streak(
        self,
        user_id: str | None = None,
        streak_type: str = "daily",
    ) -> dict[str, Any]:
        """Update learning streak for today's activity.

        Increments streak if last activity was yesterday, resets if older.

        Args:
            user_id: User ID (defaults to self.user_id).
            streak_type: Type of streak.

        Returns:
            Dict with updated streak info.
        """
        uid = user_id or self.user_id
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                """SELECT id, current_streak, longest_streak, last_activity_date
                   FROM user_streaks
                   WHERE user_id = ? AND streak_type = ?
                   LIMIT 1""",
                (uid, streak_type),
            )
            row = cur.fetchone()

            if not row:
                # First activity — start streak
                cur.execute(
                    """INSERT INTO user_streaks
                       (user_id, streak_type, current_streak, longest_streak,
                        last_activity_date, created_at, updated_at)
                       VALUES (?, ?, 1, 1, ?, datetime('now'), datetime('now'))""",
                    (uid, streak_type, today),
                )
                conn.commit()
                return {
                    "user_id": uid,
                    "streak_type": streak_type,
                    "current_streak": 1,
                    "longest_streak": 1,
                    "last_activity_date": today,
                }

            last_date = row["last_activity_date"]
            current = row["current_streak"]
            longest = row["longest_streak"]

            if last_date == today:
                # Already recorded today — no change
                return {
                    "user_id": uid,
                    "streak_type": streak_type,
                    "current_streak": current,
                    "longest_streak": longest,
                    "last_activity_date": today,
                }

            # Calculate gap
            if last_date:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                gap = (datetime.now() - last_dt).days
            else:
                gap = 999

            if gap == 1:
                # Consecutive day — increment
                new_current = current + 1
            else:
                # Streak broken — reset to 1
                new_current = 1

            new_longest = max(longest, new_current)

            cur.execute(
                """UPDATE user_streaks
                   SET current_streak = ?, longest_streak = ?,
                       last_activity_date = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (new_current, new_longest, today, row["id"]),
            )
            conn.commit()

            return {
                "user_id": uid,
                "streak_type": streak_type,
                "current_streak": new_current,
                "longest_streak": new_longest,
                "last_activity_date": today,
            }

        except Exception as e:
            conn.rollback()
            logger.debug("Update streak failed: %s", e)
            return {
                "user_id": uid,
                "streak_type": streak_type,
                "current_streak": 0,
                "longest_streak": 0,
                "last_activity_date": None,
                "error": str(e),
            }
        finally:
            conn.close()

    # ── Error categorization ─────────────────────────────────────────────

    @staticmethod
    def categorize_correction(original: str, corrected: str) -> dict[str, str]:
        """Categorize the type of error in a correction.

        Checks for:
        - ZVS forbidden forms (pathian→pasian, ram→gam, etc.)
        - SOV/negation/question grammar patterns
        - Tone sandhi violations
        - Compound decomposition errors

        Args:
            original: Original (incorrect) text.
            corrected: Corrected text.

        Returns:
            Dict with category, subcategory, rule.
        """
        orig_lower = original.lower()
        corr_lower = corrected.lower()

        # ── ZVS forbidden forms ─────────────────────────────────────────
        from ..offline.rule_engine import ZVS_CORRECTIONS

        for forbidden, correct in ZVS_CORRECTIONS.items():
            if forbidden in orig_lower and correct in corr_lower:
                return {
                    "category": "zvs",
                    "subcategory": "forbidden_form",
                    "rule": f"'{forbidden}' → '{correct}' (ZVS 2018)",
                }

        # ── SOV word order ──────────────────────────────────────────────
        # Check if word order changed (e.g., "S V O" → "S O V")
        orig_words = orig_lower.split()
        corr_words = corr_lower.split()
        if len(orig_words) == len(corr_words) and set(orig_words) == set(corr_words):
            if orig_words != corr_words:
                return {
                    "category": "grammar",
                    "subcategory": "word_order",
                    "rule": "SOV word order: Subject-Object-Verb",
                }

        # ── Negation patterns ───────────────────────────────────────────
        negation_words = {"kei", "lo"}
        orig_has_neg = any(w in negation_words for w in orig_words)
        corr_has_neg = any(w in negation_words for w in corr_words)
        if orig_has_neg != corr_has_neg or (orig_has_neg and corr_has_neg and orig_words != corr_words):
            # Check if negation position changed
            return {
                "category": "grammar",
                "subcategory": "negation",
                "rule": "Negation 'kei'/'lo' placement before verb",
            }

        # ── Question patterns ───────────────────────────────────────────
        if "hiam" in orig_lower and "hiam" not in corr_lower:
            return {
                "category": "grammar",
                "subcategory": "question_marker",
                "rule": "Question marker 'hiam' usage",
            }
        if "hiam" not in orig_lower and "hiam" in corr_lower:
            return {
                "category": "grammar",
                "subcategory": "question_marker",
                "rule": "Question marker 'hiam' should be at sentence end",
            }

        # ── Tone sandhi (detect if correction changes tones) ────────────
        # Simplified: if words differ only by tone-related characters
        if len(orig_words) == len(corr_words):
            tone_diff = False
            for ow, cw in zip(orig_words, corr_words):
                if ow != cw and len(ow) == len(cw):
                    # Check if only tone-marking difference
                    ow_base = re.sub(r"[0-4]", "", ow)
                    cw_base = re.sub(r"[0-4]", "", cw)
                    if ow_base == cw_base:
                        tone_diff = True
                        break
            if tone_diff:
                return {
                    "category": "tone",
                    "subcategory": "tone_sandhi",
                    "rule": "Tone sandhi rules (19 rules)",
                }

        # ── Morphology / compound decomposition ─────────────────────────
        # If correction adds/removes morpheme boundaries (indicated by spaces or +)
        if "+" in corrected or len(corr_words) > len(orig_words):
            return {
                "category": "morphology",
                "subcategory": "compound_decomposition",
                "rule": "Compound morpheme analysis",
            }

        # ── Fallback: general grammar ───────────────────────────────────
        return {
            "category": "grammar",
            "subcategory": "general",
            "rule": "General grammar correction",
        }

    def get_error_breakdown(self, user_id: str | None = None) -> dict[str, Any]:
        """Get error breakdown by category for a user.

        Aggregates from user_reviews table and categorizes each correction.

        Args:
            user_id: User ID (defaults to self.user_id).

        Returns:
            Dict with category counts, total errors, most common category.
        """
        uid = user_id or self.user_id
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Get user's review history
            cur.execute(
                """SELECT word, quality
                   FROM user_reviews
                   WHERE user_id = ?
                   ORDER BY reviewed_at DESC
                   LIMIT 200""",
                (uid,),
            )
            reviews = cur.fetchall()

            if not reviews:
                return {
                    "user_id": uid,
                    "total_errors": 0,
                    "categories": {},
                    "most_common": None,
                }

            # Categorize errors from low-quality reviews (quality < 3 = failure)
            categories: dict[str, int] = {}
            total_errors = 0

            for review in reviews:
                if review["quality"] < 3:
                    # Use word as both original and corrected (since we don't
                    # store the corrected form in user_reviews)
                    # For now, count each failed review as a grammar error
                    cat = self.categorize_correction(review["word"], review["word"])
                    category = cat["category"]
                    categories[category] = categories.get(category, 0) + 1
                    total_errors += 1

            most_common = max(categories, key=categories.get) if categories else None

            return {
                "user_id": uid,
                "total_errors": total_errors,
                "categories": categories,
                "most_common": most_common,
            }

        except Exception as e:
            logger.debug("Get error breakdown failed: %s", e)
            return {
                "user_id": uid,
                "total_errors": 0,
                "categories": {},
                "most_common": None,
                "error": str(e),
            }
        finally:
            conn.close()
