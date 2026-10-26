"""RED-first suite for the vocabulary ↔ evidence wiring script (G3).

Synthetic ``tmp_path`` fixtures only — the live database is never opened.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "scripts.learning.wire_vocabulary_evidence"

DDL = """
CREATE TABLE vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    headword TEXT NOT NULL,
    english TEXT NOT NULL,
    frequency INTEGER NOT NULL DEFAULT 0,
    books TEXT NOT NULL DEFAULT '[]',
    examples TEXT NOT NULL DEFAULT '[]',
    myanmar TEXT,
    version INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT,
    review_status TEXT DEFAULT 'unknown',
    confidence REAL,
    status TEXT NOT NULL DEFAULT 'OBSERVED'
);
CREATE UNIQUE INDEX ix_vocabulary_headword_unique ON vocabulary(headword);

CREATE TABLE attestation_index (
    word TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (word, source)
);

CREATE TABLE word_observation_stats (
    normalized_form TEXT PRIMARY KEY,
    frequency INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    attestation TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE word_alignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    zolai_word TEXT NOT NULL,
    english_word TEXT NOT NULL,
    position INTEGER NOT NULL
);

CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT,
    english_clean TEXT,
    is_deleted INTEGER DEFAULT 0
);

CREATE TABLE zolai_vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zolai TEXT NOT NULL,
    english TEXT,
    confidence REAL
);

CREATE TABLE word_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL,
    book TEXT NOT NULL,
    total_freq INTEGER NOT NULL,
    meaning_shifts TEXT NOT NULL DEFAULT '[]',
    co_occurring_words TEXT NOT NULL DEFAULT '[]'
);
"""

# headword, english, review_status, confidence
VOCAB = [
    ("alpha", "alpha gloss", "unknown", None),
    ("beta", "beta gloss", "unknown", None),
    ("gamma", "gamma gloss", "unknown", None),
    ("delta", "delta gloss", "unknown", None),
    ("epsilon", "epsilon gloss", "unknown", None),
    ("human", "human gloss", "reviewed", None),
    ("sanga", "31", "unknown", None),
    ("za", "a hundered", "unknown", None),
    ("livo", "lived", "unknown", None),
]

# headword, source rows
ATTESTATION = {
    "beta": ["dict"],
    "gamma": ["dict", "corpus"],
    "delta": ["dict", "corpus", "bible"],
    "epsilon": ["dict", "corpus", "bible", "extra"],
    "human": ["dict"],
    "livo": ["bible"],
}


def _load():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:  # pragma: no cover — RED until implemented
        pytest.fail(f"{MODULE} not implemented yet: {exc}")


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO vocabulary (headword, english, review_status, confidence)"
            " VALUES (?,?,?,?)",
            VOCAB,
        )
        conn.executemany(
            "INSERT INTO attestation_index (word, source) VALUES (?,?)",
            [(w, s) for w, sources in ATTESTATION.items() for s in sources],
        )
        conn.execute(
            "INSERT INTO word_observation_stats"
            " (normalized_form, frequency, source_count, attestation)"
            " VALUES (?,?,?,?)",
            ("gamma", 42, 2, '{"confidence": 0.7, "source_count": 2}'),
        )
        conn.execute(
            "INSERT INTO word_usage (word, book, total_freq) VALUES (?,?,?)",
            ("gamma", "GEN", 7),
        )
        # 'za' → alignment evidence strong for a single candidate (≥10 hits)
        conn.executemany(
            "INSERT INTO word_alignments (ref, zolai_word, english_word, position)"
            " VALUES (?,?,?,?)",
            [(f"GEN 1:{i}", "za", "hear", i) for i in range(1, 13)],
        )
        # 'livo' → two competing candidates, current gloss is supported (report path)
        conn.executemany(
            "INSERT INTO word_alignments (ref, zolai_word, english_word, position)"
            " VALUES (?,?,?,?)",
            [(f"JHN 1:{i}", "livo", "live", i) for i in range(1, 21)],
        )
        conn.executemany(
            "INSERT INTO word_alignments (ref, zolai_word, english_word, position)"
            " VALUES (?,?,?,?)",
            [(f"JHN 2:{i}", "livo", "lived", i) for i in range(1, 6)],
        )
        # 'sanga' has no alignment and a corrupted dictionary gloss
        conn.execute(
            "INSERT INTO dictionary (zolai, english, english_clean) VALUES (?,?,?)",
            ("sanga", "31", None),
        )
        conn.execute(
            "INSERT INTO zolai_vocabulary (zolai, english, confidence) VALUES (?,?,?)",
            ("sanga", "thou", 1.0),
        )
        conn.commit()
    finally:
        conn.close()


def _row(path: Path, headword: str) -> tuple:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT english, confidence, review_status, frequency, status"
            " FROM vocabulary WHERE headword=?",
            (headword,),
        ).fetchone()
    finally:
        conn.close()


def _all_rows(path: Path) -> dict[str, tuple]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT headword, english, confidence, review_status, frequency,"
            " status FROM vocabulary"
        ).fetchall()
    finally:
        conn.close()
    return {
        row["headword"]: (
            row["english"],
            row["confidence"],
            row["review_status"],
            row["frequency"],
            row["status"],
        )
        for row in rows
    }


# ── preflight guard ─────────────────────────────────────────────────────────


def test_preflight_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"")
    with pytest.raises(SystemExit) as excinfo:
        mod.preflight(decoy)
    assert "GB" in str(excinfo.value)


def test_cli_refuses_sub_gb_target(tmp_path: Path) -> None:
    mod = _load()
    decoy = tmp_path / "zolai.db"
    decoy.write_bytes(b"\0" * 8)
    assert mod.main(["--db", str(decoy)]) != 0


# ── confidence / review mapping ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sources", "confidence", "review"),
    [
        (0, 0.10, "unknown"),
        (1, 0.50, "attested"),
        (2, 0.70, "verified"),
        (3, 0.85, "verified"),
        (4, 0.95, "verified"),
    ],
)
def test_confidence_and_review_mapping(
    tmp_path: Path, sources: int, confidence: float, review: str
) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    conn = sqlite3.connect(db)
    for i in range(sources):
        conn.execute(
            "INSERT INTO attestation_index (word, source) VALUES (?,?)",
            ("probe", f"src{i}"),
        )
    conn.execute(
        "INSERT INTO vocabulary (headword, english, review_status, confidence)"
        " VALUES ('probe', 'probe gloss', 'unknown', NULL)"
    )
    conn.commit()
    conn.close()

    mod.wire(str(db))
    english, conf, review_status, _freq, _status = _row(db, "probe")
    assert english == "probe gloss"
    assert conf == pytest.approx(confidence)
    assert review_status == review


def test_observation_source_count_raises_confidence(tmp_path: Path) -> None:
    """``word_observation_stats.attestation`` source_count feeds the mapping."""
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO word_observation_stats"
        " (normalized_form, frequency, source_count, attestation)"
        " VALUES ('observed', 9, 5, '{\"confidence\": 0.95, \"source_count\": 5}')"
    )
    conn.execute(
        "INSERT INTO vocabulary (headword, english, review_status, confidence)"
        " VALUES ('observed', 'obs gloss', 'unknown', NULL)"
    )
    conn.commit()
    conn.close()
    mod.wire(str(db))
    _english, conf, review, _freq, _status = _row(db, "observed")
    assert conf == pytest.approx(0.95)
    assert review == "verified"


def test_reviewed_rows_are_never_touched(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.wire(str(db))
    assert _row(db, "human") == ("human gloss", None, "reviewed", 0, "OBSERVED")
    assert summary["skipped_reviewed"] == 1
    assert summary["confidence_updates"] > 0


def test_evidence_collection_merges_sources_freq_and_observation(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    conn = sqlite3.connect(db)
    try:
        evidence = mod.collect_evidence(conn)
    finally:
        conn.close()
    gamma = evidence["gamma"]
    assert sorted(gamma["sources"]) == ["corpus", "dict"]
    assert gamma["source_count"] == 2
    assert gamma["obs_source_count"] == 2
    assert gamma["obs_confidence"] == pytest.approx(0.7)
    assert gamma["freq"] == 42  # max(word_observation_stats.frequency, word_usage sum)
    assert evidence["alpha"]["source_count"] == 0


# ── gloss rules ─────────────────────────────────────────────────────────────


def test_corrupt_digit_gloss_is_auto_fixed(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    summary = mod.wire(str(db))
    english, _conf, _review, _freq, _status = _row(db, "sanga")
    assert english == "thou"
    assert summary["gloss_fixes"] >= 1


def test_zero_support_with_strong_candidate_is_replaced(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.wire(str(db))
    english, _conf, _review, _freq, _status = _row(db, "za")
    assert english == "hear"


def test_semantic_disagreement_is_reported_not_overwritten(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    report = tmp_path / "disagreements.jsonl"
    summary = mod.wire(str(db), report_path=report)
    english, _conf, _review, _freq, _status = _row(db, "livo")
    assert english == "lived", "non-corrupt glosses are never mass-overwritten"
    lines = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    livo = [line for line in lines if line["headword"] == "livo"]
    assert livo and livo[0]["action"] == "report"
    assert summary["gloss_reports"] >= 1


def test_status_and_frequency_columns_untouched(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _all_rows(db)
    mod.wire(str(db))
    after = _all_rows(db)
    assert set(before) == set(after)
    for headword, row in before.items():
        assert after[headword][3] == row[3] == 0, "frequency must not be rewritten"
        assert after[headword][4] == "OBSERVED", "status enum must not be invented"
        assert after[headword][2] in {"unknown", "attested", "verified", "reviewed"}
    assert after["human"][2] == "reviewed"


def test_no_null_confidence_left(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    mod.wire(str(db))
    conn = sqlite3.connect(db)
    try:
        nulls = conn.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE confidence IS NULL"
            " AND review_status <> 'reviewed'"
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM vocabulary WHERE review_status='verified'"
        ).fetchone()[0]
        values = {
            r[0]
            for r in conn.execute("SELECT DISTINCT review_status FROM vocabulary")
        }
    finally:
        conn.close()
    assert nulls == 0
    assert verified > 0
    assert values <= {"unknown", "attested", "verified", "reviewed"}


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    first = mod.wire(str(db))
    second = mod.wire(str(db))
    assert first["confidence_updates"] > 0
    assert second["confidence_updates"] == 0
    assert second["review_updates"] == 0
    assert second["gloss_fixes"] == 0


def test_dry_run_updates_nothing_and_writes_no_report(tmp_path: Path) -> None:
    mod = _load()
    db = tmp_path / "zolai.db"
    _build_db(db)
    before = _all_rows(db)
    report = tmp_path / "never.jsonl"
    summary = mod.wire(str(db), dry_run=True, report_path=report)
    assert summary["dry_run"] is True
    assert summary["confidence_updates"] > 0
    assert summary["would_update"] > 0
    assert _all_rows(db) == before
    assert not report.exists()
