"""Quarantined user-text intake contracts."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_foundation_tables, create_monitoring_tables
from zolai.foundation.user_submissions import UserTextSubmissionService


@dataclass
class FakeAnalysis:
    sentence: str
    zvs_compliant: bool
    sov_valid: bool


class FakeAnalyzer:
    def __init__(self, *analyses: FakeAnalysis) -> None:
        self.analyses = list(analyses)

    def analyze_sentence(self, sentence: str) -> FakeAnalysis:
        return self.analyses.pop(0)


def _manager(tmp_path) -> DatabaseManager:
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'submissions.db'}")
    manager.init_db()
    create_foundation_tables(manager)
    return manager


def _valid_report(valid: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        is_valid=valid,
        to_dict=lambda: {"valid": valid, "violations": [] if valid else [{"rule_id": "zvs"}]},
    )


def test_submission_is_analyzed_quarantined_and_idempotent(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    analyzer = FakeAnalyzer(FakeAnalysis("Ka pai hi", True, True))
    monkeypatch.setattr("zolai.foundation.user_submissions.validate", lambda *a, **k: _valid_report())

    service = UserTextSubmissionService(manager, analyzer)
    first = service.submit(
        "Ka pai hi.",
        "sentence",
        user_id=7,
        submitted_by="speaker",
        auth_source="session",
    )
    duplicate = service.submit("  Ka   pai hi. ", "sentence", user_id=7)

    assert first["status"] == "quarantined"
    assert first["validation_status"] == "passed"
    assert duplicate["duplicate"] is True
    assert duplicate["id"] == first["id"]
    with manager.engine.connect() as conn:
        raw = conn.execute(
            text("SELECT source_type, content_hash, payload FROM foundation_raw_corpus")
        ).first()
        candidate = conn.execute(
            text(
                "SELECT user_id, submitted_by, auth_source, analysis_json, provenance_json "
                "FROM foundation_user_submissions"
            )
        ).first()
        canonical = conn.execute(text("SELECT COUNT(*) FROM canonical_sentences")).scalar()
    assert raw[0] == "user_submission"
    assert json.loads(raw[2])["text"] == "Ka pai hi."
    assert candidate[0:3] == (7, "speaker", "session")
    assert json.loads(candidate[3])["items"]
    assert json.loads(candidate[4])["content_hash"] == first["content_hash"]
    assert canonical == 0


def test_non_zvs_and_mixed_text_remain_quarantined(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    reports = iter((_valid_report(False), _valid_report(True)))
    monkeypatch.setattr(
        "zolai.foundation.user_submissions.validate",
        lambda *a, **k: next(reports),
    )
    service = UserTextSubmissionService(
        manager,
        FakeAnalyzer(
            FakeAnalysis("Pathian pai hi", False, True),
            FakeAnalysis("Ka pai hi", True, True),
            FakeAnalysis("Ka pai kei hi", True, False),
        ),
    )

    non_zvs = service.submit("Pathian pai hi", "sentence")
    mixed = service.submit("Ka pai hi. Ka pai hi.", "article")

    assert non_zvs["status"] == "quarantined"
    assert non_zvs["validation_status"] == "non_zvs"
    assert mixed["status"] == "quarantined"
    assert mixed["validation_status"] == "mixed"


def test_submission_reuses_existing_corpus_raw_row(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    create_monitoring_tables(manager)
    with manager.engine.begin() as conn:
        raw_id = conn.execute(
            text(
                "INSERT INTO foundation_raw_corpus "
                "(source_type, source_path, content_hash, payload, imported_at) "
                "VALUES ('bible_usx', 'bible.xml', :hash, :payload, '2026-01-01')"
            ),
            {
                "hash": "placeholder",
                "payload": json.dumps({"text": "Ka pai hi."}),
            },
        ).lastrowid
        content_hash = hashlib.sha256("Ka pai hi.".encode()).hexdigest()
        conn.execute(
            text(
                "UPDATE foundation_raw_corpus SET content_hash = :hash "
                "WHERE id = :id"
            ),
            {"hash": content_hash, "id": raw_id},
        )

    monkeypatch.setattr("zolai.foundation.user_submissions.validate", lambda *a, **k: _valid_report())
    result = UserTextSubmissionService(
        manager, FakeAnalyzer(FakeAnalysis("Ka pai hi", True, True))
    ).submit("Ka pai hi.", "sentence")

    assert result["duplicate"] is False
    with manager.engine.connect() as conn:
        linked = conn.execute(
            text(
                "SELECT raw_id, content_hash FROM foundation_user_submissions"
            )
        ).first()
        raw = conn.execute(
            text("SELECT source_type, COUNT(*) FROM foundation_raw_corpus GROUP BY source_type")
        ).all()
    assert linked == (raw_id, content_hash)
    assert raw == [("bible_usx", 1)]


def test_concurrent_duplicate_submissions_return_one_candidate(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    create_monitoring_tables(manager)
    barrier = threading.Barrier(2)
    monkeypatch.setattr("zolai.foundation.user_submissions.validate", lambda *a, **k: _valid_report())

    class SynchronizedAnalyzer:
        def analyze_sentence(self, sentence: str) -> FakeAnalysis:
            barrier.wait(timeout=5)
            return FakeAnalysis(sentence, True, True)

    db_url = manager._db_url

    def submit_once():
        return UserTextSubmissionService(
            DatabaseManager(db_url), SynchronizedAnalyzer()
        ).submit("Ka pai hi.", "sentence")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit_once(), range(2)))

    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert results[0]["id"] == results[1]["id"]
