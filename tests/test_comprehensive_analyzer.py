"""Tests for ``ComprehensiveAnalyzer``'s corpus-backed sections."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zolai.api.comprehensive_analyzer import ComprehensiveAnalyzer


@pytest.fixture()
def conn(tmp_path: Path):
    db = tmp_path / "analysis.db"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE zolai_proverbs_idioms (
            zolai TEXT,
            english_translation TEXT,
            source_category TEXT,
            category TEXT
        );
        INSERT INTO zolai_proverbs_idioms VALUES
            ('duh leh ngah', 'seek and find', 'wise_saying', 'wise_saying');
        """
    )
    connection.commit()
    yield connection
    connection.close()


def _analyzer(tmp_path: Path) -> ComprehensiveAnalyzer:
    return ComprehensiveAnalyzer(str(tmp_path / "analysis.db"))


def test_idioms_read_the_canonical_table(conn, tmp_path: Path) -> None:
    """Regression: the section queried a table that no longer exists.

    ``proverbs_idioms`` raised ``no such table`` for every Zolai word; the
    canonical collection is ``zolai_proverbs_idioms`` and its translation
    column is ``english_translation``. No live row carries ``category='idiom'``
    — the table *is* the idiom corpus — so the filter is gone too.
    """
    rows = _analyzer(tmp_path)._get_idioms(conn, "duh", "zo")

    assert len(rows) == 1
    assert rows[0]["zolai"] == "duh leh ngah"
    assert rows[0]["english"] == "seek and find"
    assert rows[0]["source"] == "wise_saying"


def test_idioms_are_empty_for_non_zolai(conn, tmp_path: Path) -> None:
    assert _analyzer(tmp_path)._get_idioms(conn, "duh", "en") == []
