"""The local learning cycle: containment, bookkeeping, and atomic publish.

Three properties matter more than any metric:

* a dry-run writes the report and nothing else,
* a failed step leaves a ``FAILED`` run row, a partial report, and no version,
* a rejected model never replaces the canonical pickle.

Everything here runs on a synthetic ``tmp_path`` database — the 2.3 GB corpus
is never opened by this module.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from zolai.data.migrations import (
    EVAL_CASES_DDL,
    EVAL_RUNS_DDL,
    EVAL_SETS_DDL,
    KNOWLEDGE_VERSIONS_DDL,
    create_learning_run_tables,
)
from zolai.data.models import DataAuditLog
from zolai.learning import core_training, cycle, run_ledger
from zolai.learning.cycle import LearningCycleOptions, run_learning_cycle
from zolai.learning.run_ledger import get_run, list_runs, start_run

TABLES_DDL = """
CREATE TABLE knowledge_vectors (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    metadata TEXT,
    embedding TEXT,
    source_type TEXT,
    source TEXT,
    version INTEGER DEFAULT 1,
    imported_at TEXT
);
"""


@pytest.fixture()
def engine(tmp_path: Path):
    eng = create_engine(f"sqlite:///{tmp_path / 'cycle.db'}")
    with eng.begin() as conn:
        for ddl in (
            KNOWLEDGE_VERSIONS_DDL,
            EVAL_SETS_DDL,
            EVAL_CASES_DDL,
            EVAL_RUNS_DDL,
            TABLES_DDL,
        ):
            conn.execute(text(ddl))
    DataAuditLog.__table__.create(eng, checkfirst=True)
    create_learning_run_tables(eng)
    return eng


def _db_path(engine) -> Path:
    return Path(engine.url.database)


def _options(tmp_path: Path, engine, **overrides) -> LearningCycleOptions:
    base = {
        "db_path": _db_path(engine),
        "allow_small_db": True,
        "min_free_gb": 0.0,
        "report_dir": tmp_path / "reports",
    }
    base.update(overrides)
    return LearningCycleOptions(**base)


def _count(engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar())


def _today_prefix() -> str:
    return datetime.now(timezone.utc).strftime("v%Y.%m.%d")


# ── dry-run containment ─────────────────────────────────────────────────────


def test_dry_run_writes_only_the_report(tmp_path: Path, engine) -> None:
    opts = _options(
        tmp_path,
        engine,
        do_bible=False,
        do_promote=False,
        steps=[
            "preflight",
            "backup",
            "study",
            "knowledge_bible",
            "knowledge_promote",
            "rag_incremental",
            "version_snapshot",
            "report",
        ],
    )
    result = run_learning_cycle(engine, opts)

    assert result["status"] == "SUCCEEDED"
    assert result["mode"] == "dry_run"
    assert result["version_tag"] is None

    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name["study"]["status"] == "OK"
    assert by_name["backup"]["status"] == "SKIPPED"
    assert by_name["knowledge_bible"]["status"] == "SKIPPED"
    assert by_name["knowledge_promote"]["status"] == "SKIPPED"
    assert by_name["version_snapshot"]["status"] == "SKIPPED"
    assert by_name["rag_incremental"]["status"] == "DRY_RUN"
    # Steps excluded by --steps are recorded as skipped, not silently dropped.
    assert by_name["train_pos"]["status"] == "SKIPPED"

    assert _count(engine, "learning_runs") == 0
    assert _count(engine, "knowledge_versions") == 0
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "models").exists()

    report_path = tmp_path / "reports" / f"learning_run_{result['run_id']}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry_run"
    assert report["version_tag"] is None
    assert str(tmp_path) not in json.dumps(report)


def test_record_dry_run_writes_ledger_but_no_version(tmp_path: Path, engine) -> None:
    opts = _options(
        tmp_path,
        engine,
        record_dry_run=True,
        steps=["preflight", "study", "report"],
    )
    result = run_learning_cycle(engine, opts)

    assert result["status"] == "SUCCEEDED"
    run = get_run(engine, result["run_id"], include_steps=True)
    assert run is not None
    assert run["mode"] == "dry_run"
    assert run["status"] == "SUCCEEDED"
    # Every registry step gets a row; the three selected ones actually ran.
    assert run["steps_total"] == len(cycle.STEPS)
    assert {s["name"] for s in run["steps"] if s["status"] not in ("SKIPPED",)} == {
        "preflight",
        "study",
        "report",
    }
    # Paths never reach the ledger.
    assert str(tmp_path) not in json.dumps(run["options"])
    assert _count(engine, "knowledge_versions") == 0


def test_ledger_options_drop_paths() -> None:
    options = LearningCycleOptions(
        db_path=Path("/nonexistent/secret.db"),
        backup_dir=Path("/nonexistent/backups"),
        report_dir=Path("/nonexistent/reports"),
    )
    payload = options.ledger_options()
    assert "db_path" not in payload
    assert "backup_dir" not in payload
    assert "report_dir" not in payload


# ── failure bookkeeping ─────────────────────────────────────────────────────


def test_failed_step_marks_run_failed_and_creates_no_version(
    tmp_path: Path, engine, monkeypatch
) -> None:
    def boom(ctx):
        raise RuntimeError(
            f"cannot open {ctx.db_path} with api_key=sk-live-abcdef123456"
        )

    monkeypatch.setattr(
        cycle,
        "STEPS",
        (
            ("preflight", cycle._step_preflight),
            ("boom", boom),
            ("report", cycle._step_report),
        ),
    )
    opts = _options(tmp_path, engine, apply=True, steps=["preflight", "boom"])

    with pytest.raises(RuntimeError):
        run_learning_cycle(engine, opts)

    runs = list_runs(engine)
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "FAILED"
    assert run["error_class"] == "RuntimeError"
    assert run["steps_failed"] == 1

    with engine.connect() as conn:
        message = conn.execute(
            text("SELECT error_message FROM learning_runs WHERE run_id = :r"),
            {"r": run["run_id"]},
        ).scalar()
    assert "<db>" in message
    assert str(tmp_path) not in message
    assert "sk-live" not in message
    assert "<redacted>" in message

    assert _count(engine, "knowledge_versions") == 0

    report = json.loads(
        (tmp_path / "reports" / f"learning_run_{run['run_id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "FAILED"
    assert report["version_tag"] is None
    assert report["error_class"] == "RuntimeError"
    assert "<db>" in report["error_message"]
    assert str(tmp_path) not in json.dumps(report)
    assert "sk-live" not in json.dumps(report)


# ── apply ───────────────────────────────────────────────────────────────────


def test_apply_backs_up_and_snapshots_a_version(tmp_path: Path, engine) -> None:
    opts = _options(
        tmp_path,
        engine,
        apply=True,
        content_manifest=False,
        steps=["preflight", "backup", "study", "version_snapshot", "report"],
    )
    result = run_learning_cycle(engine, opts)

    assert result["status"] == "SUCCEEDED"
    assert result["version_tag"] == f"{_today_prefix()}.1"

    run = get_run(engine, result["run_id"])
    assert run["status"] == "SUCCEEDED"
    assert run["version_tag"] == result["version_tag"]
    assert run["backup_name"] and ".bak-" in run["backup_name"]
    assert run["backup_sha256"]
    assert run["knowledge_version_id"] is not None

    backup = tmp_path / "backups" / run["backup_name"]
    assert backup.exists()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == run["backup_sha256"]

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, source_versions, eval_run_id FROM knowledge_versions "
                "WHERE version = :v"
            ),
            {"v": result["version_tag"]},
        ).first()
    assert row is not None
    assert row[0] == "OBSERVED"
    source_versions = json.loads(row[1])
    assert source_versions["learning_run"] == result["run_id"]
    assert row[2] == run["knowledge_version_id"]
    assert str(tmp_path) not in json.dumps(source_versions)

    # A second run the same day gets the next suffix, not a collision.
    second = run_learning_cycle(engine, opts)
    assert second["version_tag"] == f"{_today_prefix()}.2"
    assert _count(engine, "knowledge_versions") == 2


def test_snapshot_retries_when_another_writer_drifts_the_db(
    tmp_path: Path, engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit landing between hashing and verification must not fail the run.

    On the shared corpus another agent's cycle wrote ``word_usage`` seconds
    after the snapshot; verify saw drift and the run aborted with its earlier
    writes already committed. A fresh snapshot is a true observation, so the
    step takes one; the drifted row stays behind as history.
    """
    import zolai.knowledge.versioning as versioning

    real_verify = versioning.verify_knowledge_version
    calls = {"n": 0}

    def flaky_verify(engine_, tag):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False,
                "version": tag,
                "drift": {"word_usage": {"recorded": "a", "live": "b"}},
            }
        return real_verify(engine_, tag)

    monkeypatch.setattr(versioning, "verify_knowledge_version", flaky_verify)
    monkeypatch.setattr("zolai.learning.cycle.time.sleep", lambda _s: None)

    opts = _options(
        tmp_path,
        engine,
        apply=True,
        content_manifest=False,
        steps=["preflight", "backup", "study", "version_snapshot", "report"],
    )
    result = run_learning_cycle(engine, opts)

    assert result["status"] == "SUCCEEDED"
    assert calls["n"] == 2
    assert result["version_tag"] == f"{_today_prefix()}.2"
    assert _count(engine, "knowledge_versions") == 2


# ── model artifacts ─────────────────────────────────────────────────────────


def test_bible_step_reports_a_ref_for_the_owner_review_file(
    tmp_path: Path, engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary is copied into the report and the ledger verbatim."""
    owner_file = tmp_path / "reports" / "vocab_gloss_disagreements_lr_x.jsonl"

    monkeypatch.setattr(
        "scripts.learning.repair_and_learn_bible_word_usage.learn_bible",
        lambda *_a, **_k: {"dry_run": True},
    )
    monkeypatch.setattr(
        "scripts.learning.wire_vocabulary_evidence.wire",
        lambda *_a, **_k: {"report_path": str(owner_file), "linked": 0},
    )
    monkeypatch.setattr(
        "scripts.maintenance.dedupe_bible_verses_import.dedupe",
        lambda *_a, **_k: {"table_present": False, "skipped": "absent"},
    )
    monkeypatch.setattr(
        "scripts.learning.learn_grammar_from_bible.learn",
        lambda *_a, **_k: {"dry_run": True},
    )

    opts = _options(tmp_path, engine, steps=["knowledge_bible", "report"])
    result = run_learning_cycle(engine, opts)

    bible = next(step for step in result["steps"] if step["name"] == "knowledge_bible")
    wire = bible["metrics"]["steps"]["wire_vocabulary"]
    assert wire["report_path"] == owner_file.name
    assert str(tmp_path) not in json.dumps(result)


def _seed_pos_gold(engine, sentences: int = 12) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eval_sets (set_name, description) "
                "VALUES ('pos_gold_v0', 'pos gold') ON CONFLICT DO NOTHING"
            )
        )
        for ordinal in range(sentences):
            tokens = [
                {"text": "ka", "pos": "PRON"},
                {"text": "pai", "pos": "VERB"},
                {"text": "tui", "pos": "NOUN"},
            ]
            conn.execute(
                text(
                    "INSERT INTO eval_cases (set_name, kind, payload, ordinal, is_active) "
                    "VALUES ('pos_gold_v0', 'pos', :p, :o, 1)"
                ),
                {
                    "p": json.dumps(
                        {"text": "ka pai tui", "tokens": tokens}, ensure_ascii=False
                    ),
                    "o": ordinal,
                },
            )


def _seed_baseline(engine, metric: float) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eval_sets (set_name, description) "
                "VALUES ('pos_gold_v0', 'pos gold') ON CONFLICT DO NOTHING"
            )
        )
        conn.execute(
            text(
                "INSERT INTO eval_runs (set_name, created_at, case_count, metrics) "
                "VALUES ('pos_gold_v0', '2026-01-01T00:00:00+00:00', 1, :m)"
            ),
            {"m": json.dumps({"macro_f1": metric})},
        )


def test_pos_gate_rejection_leaves_canonical_untouched(tmp_path: Path, engine) -> None:
    pytest.importorskip("sklearn_crfsuite")
    _seed_pos_gold(engine)
    # An impossible baseline guarantees the drop gate fires, whatever the model scores.
    _seed_baseline(engine, 99.0)

    canonical = tmp_path / "models" / "pos_tagger_crf.pkl"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"old-model")

    result = core_training.train_and_publish_pos(
        engine,
        run_id="lr_test_reject",
        staging_dir=tmp_path / "runs" / "lr_test_reject" / "pos",
        canonical_path=canonical,
    )

    assert result["artifact_status"] == "REJECTED"
    assert result["staging_ref"] is None
    assert result["sha256"] is None
    assert canonical.read_bytes() == b"old-model"
    assert result["gate"]["passed"] is False
    assert result["eval_run_id"] is not None

    with engine.connect() as conn:
        gate_passed = conn.execute(
            text("SELECT gate_passed FROM eval_runs WHERE id = :i"),
            {"i": result["eval_run_id"]},
        ).scalar()
    assert gate_passed == 0


def test_pos_publish_replaces_canonical_and_keeps_previous(tmp_path: Path, engine) -> None:
    pytest.importorskip("sklearn_crfsuite")
    _seed_pos_gold(engine)

    canonical = tmp_path / "models" / "pos_tagger_crf.pkl"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"old-model")
    staging_dir = tmp_path / "runs" / "lr_test_publish" / "pos"

    result = core_training.train_and_publish_pos(
        engine,
        run_id="lr_test_publish",
        staging_dir=staging_dir,
        canonical_path=canonical,
    )

    assert result["artifact_status"] == "PUBLISHED"
    assert result["gate"]["passed"] is True
    assert canonical.read_bytes() != b"old-model"
    assert result["sha256"] == hashlib.sha256(canonical.read_bytes()).hexdigest()
    assert result["previous_sha256"] == hashlib.sha256(b"old-model").hexdigest()
    previous = staging_dir / "previous-pos_tagger_crf.pkl"
    assert previous.read_bytes() == b"old-model"
    assert (staging_dir / "manifest.json").exists()

    with engine.connect() as conn:
        gate_passed = conn.execute(
            text("SELECT gate_passed FROM eval_runs WHERE id = :i"),
            {"i": result["eval_run_id"]},
        ).scalar()
    assert gate_passed == 1


def test_training_result_carries_refs_not_absolute_paths(tmp_path: Path, engine) -> None:
    """The result is copied verbatim into the report and the ledger."""
    pytest.importorskip("sklearn_crfsuite")
    _seed_pos_gold(engine)

    staging_dir = tmp_path / "runs" / "lr_test_refs" / "pos"
    result = core_training.train_and_publish_pos(
        engine,
        run_id="lr_test_refs",
        staging_dir=staging_dir,
        canonical_path=tmp_path / "models" / "pos_tagger_crf.pkl",
    )

    assert not Path(result["staging_ref"]).is_absolute()
    if result["published_ref"] is not None:
        assert not Path(result["published_ref"]).is_absolute()
    assert "model_path" not in result["metrics"]
    assert str(tmp_path) not in str(result)


def test_discovery_capability_failure_warns_instead_of_ok(
    tmp_path: Path, engine, monkeypatch
) -> None:
    import zolai.learning.discovery.pipeline as discovery_mod

    def boom(eng, **kwargs):
        raise RuntimeError("pos builder exploded")

    monkeypatch.setattr(discovery_mod, "build_pos_hypotheses", boom)
    result = run_learning_cycle(
        engine, _options(tmp_path, engine, steps=["knowledge_discovery"])
    )

    step = next(s for s in result["steps"] if s["name"] == "knowledge_discovery")
    assert step["status"] == "WARN"
    assert result["warnings"] == 1
    assert "pos" in step["metrics"]["failed_capabilities"]


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI unavailable")
def test_preflight_points_at_reap_for_a_stale_runner(tmp_path, engine, monkeypatch) -> None:
    """A zombie RUNNING row should say it is stale, not just "still running"."""
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(timespec="seconds")
    monkeypatch.setattr(run_ledger, "utc_now", lambda: old)
    start_run(engine, mode="apply", options={}, run_id="lr_zombie")
    monkeypatch.undo()

    ctx = cycle.CycleContext(
        engine=engine,
        db_path=_db_path(engine),
        run_id="lr_next",
        options=_options(tmp_path, engine),
    )
    with pytest.raises(cycle.PreflightError) as blocked:
        cycle._step_preflight(ctx)
    message = str(blocked.value)
    assert "lr_zombie" in message
    assert "idle for" in message
    assert "zolai learn reap --apply" in message


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI unavailable")
def test_preflight_keeps_the_plain_message_for_a_live_runner(tmp_path, engine) -> None:
    start_run(engine, mode="apply", options={}, run_id="lr_live")
    ctx = cycle.CycleContext(
        engine=engine,
        db_path=_db_path(engine),
        run_id="lr_next",
        options=_options(tmp_path, engine),
    )
    with pytest.raises(cycle.PreflightError) as blocked:
        cycle._step_preflight(ctx)
    message = str(blocked.value)
    assert "finish it or pass --force" in message
    assert "idle for" not in message
