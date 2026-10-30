"""SM-2 review state belongs to the learner, not to the corpus.

`ProgressTracker` used to keep `ease_factor` / `interval` / `repetitions` /
`next_review` on the shared ``vocabulary`` row (no user column), while every
statistics query read ``user_reviews`` — which nothing wrote.  Result: two
learners overwrote each other's schedule, an unknown word inserted a bare
corpus row, and learner stats were always zero.  These tests pin the fixed
contract on a throwaway database.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import run_all_migrations
from zolai.learning.progress import ProgressTracker
from zolai.shared.clock import now, stamp


@pytest.fixture()
def db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "zolai.db"
    mgr = DatabaseManager(f"sqlite:///{path}")
    mgr.init_db()
    run_all_migrations(mgr)
    monkeypatch.setattr(config.paths, "db", path)
    yield path
    mgr.dispose()


def _rows(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_a_review_is_appended_to_user_reviews_only(db: Path) -> None:
    before = _rows(db, "SELECT COUNT(*) FROM vocabulary")[0][0]
    result = ProgressTracker("alice").update_review("pasian", 4)

    assert result["interval"] == 1 and result["repetitions"] == 1
    rows = _rows(db, "SELECT user_id, word, quality, repetitions FROM user_reviews")
    assert rows == [("alice", "pasian", 4, 1)]
    # No bare headword row is inserted into the corpus for an unknown word.
    assert _rows(db, "SELECT COUNT(*) FROM vocabulary")[0][0] == before


def test_sm2_intervals_follow_the_sequence(db: Path) -> None:
    tracker = ProgressTracker("alice")
    assert tracker.update_review("pasian", 4)["interval"] == 1
    assert tracker.update_review("pasian", 4)["interval"] == 6
    third = tracker.update_review("pasian", 4)
    assert third["interval"] == 15  # ceil(6 * 2.5): quality 4 leaves ease at 2.5
    assert third["repetitions"] == 3
    assert tracker.update_review("pasian", 1)["repetitions"] == 0


def test_two_learners_do_not_share_a_schedule(db: Path) -> None:
    ProgressTracker("alice").update_review("pasian", 5)
    assert ProgressTracker("bob").get_next_review("pasian")["status"] == "new"
    assert ProgressTracker("alice").get_next_review("pasian")["status"] == "review"


def test_due_reviews_use_the_latest_row_and_the_due_instant(db: Path) -> None:
    tracker = ProgressTracker("alice")
    tracker.update_review("pasian", 5)  # due tomorrow
    past = stamp(now() - timedelta(days=1))
    future = stamp(now() + timedelta(days=30))
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO user_reviews (user_id, word, quality, reviewed_at, next_review, "
            "ease_factor, interval, repetitions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice", "gam", 4, past, past, 2.5, 1, 1),
        )
        conn.execute(
            "INSERT INTO user_reviews (user_id, word, quality, reviewed_at, next_review, "
            "ease_factor, interval, repetitions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bob", "lei", 4, past, past, 2.5, 1, 1),
        )
        conn.commit()
    finally:
        conn.close()

    due = tracker.get_due_reviews()
    assert [row["word"] for row in due] == ["gam"]

    # A newer, not-yet-due row supersedes the older due one.
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO user_reviews (user_id, word, quality, reviewed_at, next_review, "
            "ease_factor, interval, repetitions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice", "gam", 5, stamp(), future, 2.7, 6, 2),
        )
        conn.commit()
    finally:
        conn.close()
    assert tracker.get_due_reviews() == []


def test_statistics_report_the_learner_not_the_corpus(db: Path) -> None:
    tracker = ProgressTracker("alice")
    tracker.update_review("pasian", 5)
    tracker.update_review("gam", 2)
    stats = tracker.get_statistics()

    assert stats["reviews"]["user_id"] == "alice"
    assert stats["reviews"]["total"] == 2
    assert stats["reviews"]["words_reviewed"] == 2
    assert stats["reviews"]["passed"] == 1
    assert stats["reviews"]["success_rate"] == 0.5
    assert stats["reviews"]["due_now"] == 0  # both cards are due tomorrow


def test_a_review_extends_the_streak(db: Path) -> None:
    yesterday = (now().date() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO user_streaks (user_id, streak_type, current_streak, longest_streak, "
            "last_activity_date) VALUES ('alice', 'daily', 4, 4, ?)",
            (yesterday,),
        )
        conn.commit()
    finally:
        conn.close()

    ProgressTracker("alice").update_review("pasian", 4)
    streak = ProgressTracker("alice").get_streak()
    assert streak["current_streak"] == 5
    assert streak["longest_streak"] == 5
    assert streak["is_active"] is True

    # A second review the same day does not double-count.
    ProgressTracker("alice").update_review("gam", 4)
    assert ProgressTracker("alice").get_streak()["current_streak"] == 5


def test_a_gap_resets_the_streak(db: Path) -> None:
    long_ago = (now().date() - timedelta(days=10)).isoformat()
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO user_streaks (user_id, streak_type, current_streak, longest_streak, "
            "last_activity_date) VALUES ('alice', 'daily', 9, 12, ?)",
            (long_ago,),
        )
        conn.commit()
    finally:
        conn.close()

    ProgressTracker("alice").update_review("pasian", 4)
    streak = ProgressTracker("alice").get_streak()
    assert streak["current_streak"] == 1
    assert streak["longest_streak"] == 12


def test_quality_outside_zero_to_five_is_refused(db: Path) -> None:
    with pytest.raises(ValueError):
        ProgressTracker("alice").update_review("pasian", 6)


def test_adaptive_difficulty_uses_real_error_rate_and_ease(db: Path) -> None:
    """It used to hardcode error_rate=0.0 / avg_ease=2.5, so difficulty never adapted."""
    struggling = ProgressTracker("alice")
    for _ in range(3):
        struggling.update_review("pasian", 1)
    result = struggling.get_adaptive_difficulty()
    assert result["error_rate"] == 1.0
    assert result["avg_ease"] < 2.0
    assert result["difficulty"] == "beginner"

    confident = ProgressTracker("bob")
    confident.update_review("pasian", 5)
    result = confident.get_adaptive_difficulty()
    assert result["error_rate"] == 0.0
    assert result["avg_ease"] == 2.6  # quality 5 leaves ease at 2.5 + 0.1


def test_cefr_level_counts_mastered_reviews_not_corpus_frequency(db: Path) -> None:
    """A high-frequency corpus word is not "known" until the learner masters it."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO vocabulary (headword, english, frequency, books, examples) "
            "VALUES ('pasian', 'God', 500, '[]', '[]'), ('gam', 'land', 5, '[]', '[]')"
        )
        conn.commit()
    finally:
        conn.close()

    tracker = ProgressTracker("alice")
    assert tracker.get_cefr_level()["words_known"] == 0

    # 1, 6, 15, 38 days — the fourth successful review is mature.
    for _ in range(4):
        tracker.update_review("pasian", 4)

    level = tracker.get_cefr_level()
    assert level["words_known"] == 1
    assert level["words_total"] == 2
    assert level["progress"] == 0.5
    assert level["level"] == "C1"


def test_quiz_level_filters_the_pool(db: Path) -> None:
    """``level`` reached the API but was ignored, so every learner got the full pool."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO vocabulary (headword, english, frequency, books, examples) "
            "VALUES ('pasian', 'God', 2000, '[]', '[]'), "
            "('gam', 'land', 50, '[]', '[]'), "
            "('vantung', 'sky', 1, '[]', '[]')"
        )
        conn.commit()
    finally:
        conn.close()

    tracker = ProgressTracker("alice")
    assert {q["word"] for q in tracker.generate_quiz(count=10, level="A1")} == {"pasian"}
    # Case-insensitive, and a level is a ceiling: B1 admits A1..B1.
    assert {q["word"] for q in tracker.generate_quiz(count=10, level="b1")} == {"pasian", "gam"}
    # An unknown level is ignored rather than filtering with a different rule.
    assert len(tracker.generate_quiz(count=10, level="Q9")) == 3
