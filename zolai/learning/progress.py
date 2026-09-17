"""Spaced repetition and progress tracking."""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..config import config

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Track learning progress with SM-2 spaced repetition.

    Features:
    - SM-2 algorithm for scheduling reviews
    - CEFR level tracking (A1-C2)
    - Quiz generation from vocab table
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
                   WHERE headword = ? AND user_id = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (word, self.user_id),
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
            # Get current review data
            cur.execute(
                """SELECT * FROM vocabulary
                   WHERE headword = ? AND user_id = ?
                   ORDER BY updated_at DESC LIMIT 1""",
                (word, self.user_id),
            )
            row = cur.fetchone()

            if row:
                ease_factor = row.get("ease_factor", 2.5)
                interval = row.get("interval", 0)
                repetitions = row.get("repetitions", 0)
            else:
                ease_factor = 2.5
                interval = 0
                repetitions = 0

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

            # Update or insert
            if row:
                cur.execute(
                    """UPDATE training_exercises
                       SET ease_factor = ?, interval = ?, repetitions = ?,
                           next_review = ?, updated_at = datetime('now')
                       WHERE headword = ? AND user_id = ?""",
                    (ease_factor, interval, repetitions, next_review.isoformat(), word, self.user_id),
                )
            else:
                cur.execute(
                    """INSERT INTO training_exercises
                       (headword, user_id, ease_factor, interval, repetitions, next_review)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (word, self.user_id, ease_factor, interval, repetitions, next_review.isoformat()),
                )

            conn.commit()

            return {
                "word": word,
                "next_review": next_review.isoformat(),
                "interval": interval,
                "ease_factor": round(ease_factor, 2),
                "repetitions": repetitions,
                "quality": quality,
            }

        except Exception as e:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_cefr_level(self) -> dict[str, Any]:
        """Get user's CEFR level based on vocabulary掌握.

        Returns:
            Dict with level, words_known, words_total, progress.
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Count total vocab
            cur.execute("SELECT COUNT(*) FROM vocabulary")
            total = cur.fetchone()[0]

            # Count words with good review (ease_factor > 2.0, repetitions > 2)
            cur.execute(
                """SELECT COUNT(DISTINCT headword) FROM vocabulary
                   WHERE user_id = ? AND ease_factor > 2.0 AND repetitions > 2""",
                (self.user_id,),
            )
            known = cur.fetchone()[0]

            # Calculate level
            if total == 0:
                level = "A1"
                progress = 0.0
            else:
                ratio = known / total
                if ratio < 0.1:
                    level = "A1"
                elif ratio < 0.2:
                    level = "A2"
                elif ratio < 0.35:
                    level = "B1"
                elif ratio < 0.5:
                    level = "B2"
                elif ratio < 0.7:
                    level = "C1"
                else:
                    level = "C2"
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
            query = "SELECT headword, english, frequency FROM vocabularyulary"
            params = []

            if level:
                query += " WHERE level = ?"
                params.append(level)

            query += " ORDER BY RANDOM() LIMIT ?"
            params.append(count * 2)  # Get extra for variety

            cur.execute(query, params)
            rows = cur.fetchall()

            quiz = []
            for row in rows[:count]:
                if quiz_type == "vocabulary":
                    quiz.append({
                        "question": f"What is the English meaning of '{row['word']}'?",
                        "answer": row["english"],
                        "word": row["word"],
                        "options": self._get_distractors(row["english"], "english"),
                    })
                elif quiz_type == "translation":
                    quiz.append({
                        "question": f"Translate to Zolai: '{row['english']}'",
                        "answer": row["zolai"],
                        "word": row["word"],
                        "options": self._get_distractors(row["zolai"], "zolai"),
                    })
                elif quiz_type == "reverse":
                    quiz.append({
                        "question": f"What does '{row['zolai']}' mean?",
                        "answer": row["english"],
                        "word": row["word"],
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
                """SELECT headword, ease_factor, interval, repetitions, next_review
                   FROM vocabulary
                   WHERE user_id = ? AND next_review <= datetime('now')
                   ORDER BY next_review ASC
                   LIMIT ?""",
                (self.user_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.debug("Get due reviews failed: %s", e)
            return []
        finally:
            conn.close()
