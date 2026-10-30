"""The learning-run ledger: idempotent DDL, safer error text, tag sequencing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from zolai.data.migrations import (
    EVAL_RUNS_DDL,
    KNOWLEDGE_VERSIONS_DDL,
    create_learning_run_tables,
)
from zolai.learning import run_ledger
from zolai.learning.run_ledger import (
    allocate_version_tag,
    display_ref,
    ensure_learning_run_tables,
    finish_run,
    get_run,
    list_runs,
    new_run_id,
    reap_stale_runs,
    record_artifact,
    record_step,
    record_vector_state,
    sanitize_error,
    stale_running_runs,
    start_run,
    validate_run_id,
)


def _bare_engine(path: Path):
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        conn.execute(text(KNOWLEDGE_VERSIONS_DDL))
        conn.execute(text(EVAL_RUNS_DDL))
    return eng


@pytest.fixture()
def engine(tmp_path: Path):
    eng = _bare_engine(tmp_path / "ledger.db")
    create_learning_run_tables(eng)
    return eng


def test_table_creation_is_idempotent(tmp_path: Path) -> None:
    engine = _bare_engine(tmp_path / "idempotent.db")
    first = create_learning_run_tables(engine)
    assert set(first["created"]) >= {
        "learning_runs",
        "learning_run_steps",
        "learning_artifacts",
        "learning_vectors_state",
    }
    assert first["errors"] == []

    second = ensure_learning_run_tables(engine)
    assert second["created"] == []
    assert second["errors"] == []


def test_run_lifecycle_and_step_counts(engine) -> None:
    run_id = start_run(
        engine,
        mode="apply",
        options={"limit": 20},
        git_commit="abc1234",
        db_size_bytes=1234,
        run_id="lr_test_1",
    )
    assert run_id == "lr_test_1"

    record_step(engine, run_id, step_no=1, name="preflight", status="OK")
    record_step(engine, run_id, step_no=2, name="study", status="WARN")
    record_step(engine, run_id, step_no=3, name="train_pos", status="DRY_RUN")
    # Re-recording the same step updates instead of duplicating.
    record_step(engine, run_id, step_no=2, name="study", status="OK")

    finish_run(engine, run_id, status="SUCCEEDED", elapsed_seconds=1.5)

    run = get_run(engine, run_id)
    assert run["status"] == "SUCCEEDED"
    assert run["steps_total"] == 3
    assert run["steps_ok"] == 3
    assert run["warnings"] == 0
    assert [s["name"] for s in run["steps"]] == ["preflight", "study", "train_pos"]
    assert run["options"] == {"limit": 20}

    history = list_runs(engine)
    assert history[0]["run_id"] == run_id


def test_failed_run_records_counts_and_sanitized_error(engine, tmp_path) -> None:
    db_path = tmp_path / "ledger.db"
    run_id = start_run(engine, mode="apply", options={}, run_id="lr_test_2")
    record_step(engine, run_id, step_no=1, name="boom", status="FAILED")
    finish_run(
        engine,
        run_id,
        status="FAILED",
        error=RuntimeError(f"open {db_path} failed"),
        db_path=db_path,
    )

    run = get_run(engine, run_id)
    assert run["steps_failed"] == 1
    assert run["error_class"] == "RuntimeError"
    # get_run never returns raw error text.
    assert "error_message" not in run
    with engine.connect() as conn:
        message = conn.execute(
            text("SELECT error_message FROM learning_runs WHERE run_id = :r"),
            {"r": run_id},
        ).scalar()
    assert str(tmp_path) not in message
    assert "<db>" in message


def test_artifact_and_vector_state_upsert(engine) -> None:
    run_id = start_run(engine, mode="apply", options={}, run_id="lr_test_3")

    record_artifact(
        engine,
        run_id,
        engine_name="pos_crf",
        artifact_name="pos_tagger_crf.pkl",
        staging_ref="/home/x/repo/models/runs/r1/pos_crf.pkl",
        sha256="a" * 64,
        size_bytes=10,
        status="STAGED",
    )
    record_artifact(
        engine,
        run_id,
        engine_name="pos_crf",
        artifact_name="pos_tagger_crf.pkl",
        staging_ref="/home/x/repo/models/runs/r1/pos_crf.pkl",
        sha256="b" * 64,
        size_bytes=11,
        status="PUBLISHED",
        published_ref="/home/x/repo/models/pos_tagger_crf.pkl",
    )
    record_vector_state(
        engine,
        run_id,
        source_table="bible_verses",
        source_id="GEN 1:1",
        action="failed",
        status="failed",
        chunks=2,
        error_class="RuntimeError",
    )
    record_vector_state(
        engine,
        run_id,
        source_table="bible_verses",
        source_id="GEN 1:1",
        action="reembed",
        status="ok",
        vectors_before=1,
        vectors_after=2,
        chunks=2,
    )

    with engine.connect() as conn:
        artifacts = conn.execute(
            text("SELECT sha256, status, staging_ref, published_ref FROM learning_artifacts")
        ).fetchall()
        states = conn.execute(
            text("SELECT action, status, vectors_after FROM learning_vectors_state")
        ).fetchall()

    assert len(artifacts) == 1
    assert artifacts[0][0] == "b" * 64
    assert artifacts[0][1] == "PUBLISHED"
    # Paths are stored repo-relative / basename only.
    assert not artifacts[0][2].startswith("/home")
    assert not artifacts[0][3].startswith("/home")

    assert len(states) == 1
    assert tuple(states[0]) == ("reembed", "ok", 2)


def test_version_tag_sequencing(engine) -> None:
    moment = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert allocate_version_tag(engine, moment=moment) == "v2026.10.27.1"

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO knowledge_versions (version) VALUES ('v2026.10.27')")
        )
    assert allocate_version_tag(engine, moment=moment) == "v2026.10.27.1"

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO knowledge_versions (version) VALUES ('v2026.10.27.1')")
        )
    assert allocate_version_tag(engine, moment=moment) == "v2026.10.27.2"

    # A different day starts at suffix 1 regardless of prior days.
    other = datetime(2026, 10, 28, tzinfo=timezone.utc)
    assert allocate_version_tag(engine, moment=other) == "v2026.10.28.1"
    # Non-matching shapes are ignored, not crashed on.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO knowledge_versions (version) VALUES ('v2026.10.28-legacy')")
        )
    assert allocate_version_tag(engine, moment=other) == "v2026.10.28.1"


def test_sanitize_error_redacts_paths_and_secrets(tmp_path) -> None:
    db_path = tmp_path / "zolai.db"
    error_class, message = sanitize_error(
        RuntimeError(
            f"cannot open {db_path}: password=hunter2 "
            f"home={Path.home()} repo={run_ledger.config.paths.root}"
        ),
        db_path=db_path,
    )
    assert error_class == "RuntimeError"
    assert str(tmp_path) not in message
    assert str(Path.home()) not in message
    assert str(run_ledger.config.paths.root) not in message
    assert "hunter2" not in message
    assert "<db>" in message
    assert "password=<redacted>" in message

    long_class, long_message = sanitize_error(RuntimeError("x" * 900))
    assert long_class == "RuntimeError"
    assert len(long_message) <= 500
    assert long_message.endswith("...")


def test_display_ref_and_run_ids(tmp_path) -> None:
    assert display_ref(run_ledger.config.paths.root / "data" / "x.json") == "data/x.json"
    outside = display_ref(tmp_path / "somewhere" / "run.json")
    assert outside == "run.json"

    assert validate_run_id("lr_20261028T000000Z_abcdef") == "lr_20261028T000000Z_abcdef"
    for bad in ("", "../etc/passwd", "a b", "x" * 81):
        with pytest.raises(ValueError):
            validate_run_id(bad)

    generated = new_run_id(datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc))
    assert generated.startswith("lr_20261028T010203Z_")
    assert validate_run_id(generated) == generated


# ── stale RUNNING detection ──────────────────────────────────────────────────


def test_running_placeholder_never_looks_finished(engine) -> None:
    run_id = start_run(engine, mode="apply", options={}, run_id="lr_placeholder")
    record_step(engine, run_id, step_no=1, name="rag", status="RUNNING")

    step = get_run(engine, run_id)["steps"][0]
    assert step["status"] == "RUNNING"
    assert step["started_at"]
    assert step["finished_at"] is None

    # The terminal record upserts the same row; no duplicate placeholder stays.
    record_step(engine, run_id, step_no=1, name="rag", status="OK")
    steps = get_run(engine, run_id)["steps"]
    assert [(s["name"], s["status"]) for s in steps] == [("rag", "OK")]
    assert steps[0]["finished_at"] is not None


def test_stale_run_is_reported_and_reaped_once(engine) -> None:
    run_id = start_run(engine, mode="apply", options={}, run_id="lr_stale")
    record_step(engine, run_id, step_no=1, name="preflight", status="OK")

    later = datetime.now(timezone.utc) + timedelta(hours=7)
    stale = stale_running_runs(engine, now=later)
    assert [s["run_id"] for s in stale] == [run_id]
    assert stale[0]["idle_seconds"] > 6 * 3600
    assert stale[0]["last_activity_at"]

    assert reap_stale_runs(engine, now=later) == [run_id]
    row = get_run(engine, run_id)
    assert row["status"] == "ABORTED"
    assert row["summary"]["stale"] is True
    assert row["summary"]["last_activity_at"]
    assert row["error_class"] == "RuntimeError"
    # The reaper recounts steps like finish_run does.
    assert row["steps_total"] == 1
    assert row["steps_ok"] == 1

    # Reaping is idempotent: the run is no longer RUNNING.
    assert reap_stale_runs(engine, now=later) == []
    assert get_run(engine, run_id)["status"] == "ABORTED"


def test_recent_step_activity_keeps_a_long_run_fresh(engine) -> None:
    fresh = start_run(engine, mode="apply", options={}, run_id="lr_long")
    dead = start_run(engine, mode="apply", options={}, run_id="lr_dead")
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    recent = (later - timedelta(minutes=30)).isoformat(timespec="seconds")
    record_step(
        engine,
        fresh,
        step_no=1,
        name="train_pos",
        status="OK",
        started_at=recent,
        finished_at=recent,
    )
    record_step(engine, dead, step_no=1, name="preflight", status="OK")

    # Only the run with no recent step is stale; the long run with a fresh
    # step timestamp survives even though it started well before the cutoff.
    assert [s["run_id"] for s in stale_running_runs(engine, now=later)] == [dead]


def test_reaper_never_overwrites_a_terminal_run(engine) -> None:
    run_id = start_run(engine, mode="dry_run", options={}, run_id="lr_done")
    record_step(engine, run_id, step_no=1, name="preflight", status="OK")
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert stale_running_runs(engine, now=later)

    finish_run(engine, run_id, status="SUCCEEDED")
    assert reap_stale_runs(engine, now=later) == []
    row = get_run(engine, run_id)
    assert row["status"] == "SUCCEEDED"
    assert row["summary"].get("stale") is None


def test_terminal_runs_are_not_scanned(engine) -> None:
    run_id = start_run(engine, mode="dry_run", options={}, run_id="lr_finished")
    finish_run(engine, run_id, status="FAILED")
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert stale_running_runs(engine, now=later) == []
