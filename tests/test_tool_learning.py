"""The learning-cycle tool family: admin-only, read-only, path-free.

The three ``learning_*`` tools exist so an operator can ask *what ran, what it
published, what the vector index looks like* over MCP without a shell.  This
file pins the contract that makes that safe:

- **classification** — every tool is ``admin_only``, ``writes=False``,
  ``destructive=False``, and absent from the member tier;
- **scope** — each carries an exact action from the frozen vocabulary
  (``pipeline:read``, ``eval:read``, ``rag:read``);
- **no leaks** — the ledger stores artifact paths and step metrics verbatim;
  a response must contain neither an absolute path nor a secret-shaped value,
  and never the ``options`` blob;
- **read-only** — ``check_stale`` counts stale source ids and deletes nothing.

Isolated engine in ``tmp_path``: no live corpus, no network.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from zolai.agent.tools import learning_runs
from zolai.agent.tools.learning_runs import (
    LEARNING_RUN_HANDLERS,
    LEARNING_RUN_SPECS,
    LEARNING_RUN_TOOL_NAMES,
)
from zolai.agent.tools.registry import allow_list, get_tool
from zolai.agent.tools.types import ToolError, ToolErrorCode
from zolai.data.migrations import (
    EVAL_RUNS_DDL,
    EVAL_SETS_DDL,
    KNOWLEDGE_VERSIONS_DDL,
    create_learning_run_tables,
)
from zolai.learning.run_ledger import finish_run, record_step, start_run

_FAKE_SECRET = "sk-test-not-a-real-key"  # unmistakable fake, per Secret Hygiene


def _build_engine(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for ddl in (KNOWLEDGE_VERSIONS_DDL, EVAL_SETS_DDL, EVAL_RUNS_DDL):
            conn.execute(text(ddl))
        conn.execute(
            text(
                "CREATE TABLE knowledge_vectors ("
                "id TEXT PRIMARY KEY, source TEXT, metadata TEXT, imported_at TEXT)"
            )
        )
        conn.execute(text("CREATE TABLE phrases (id INTEGER PRIMARY KEY, zolai TEXT)"))
        conn.execute(text("INSERT INTO phrases (id, zolai) VALUES (1, 'tawm')"))
        conn.execute(text("INSERT INTO phrases (id, zolai) VALUES (2, 'pai')"))
    create_learning_run_tables(engine)
    return engine


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    engine = _build_engine(tmp_path / "tools.db")
    monkeypatch.setattr(learning_runs, "_engine", lambda: engine)
    return engine


def _seed_run(engine, *, run_id="lr_20261028T000000Z_abcdef") -> str:
    start_run(
        engine,
        mode="apply",
        options={"apply": True, "limit": 5},
        git_commit="abc1234",
        run_id=run_id,
    )
    record_step(
        engine,
        run_id,
        step_no=1,
        name="train_pos",
        status="OK",
        metrics={
            "artifact_status": "PUBLISHED",
            "staging_ref": "/home/peter/secret-place/best.pkl",
            "published_ref": "/home/peter/secret-place/pos_tagger_crf.pkl",
            "location": "/home/peter/secret-place/notes.txt",
            "sha256": "a" * 64,
            "note": f"api_key={_FAKE_SECRET}",
        },
        elapsed_seconds=1.5,
    )
    finish_run(engine, run_id, status="SUCCEEDED", elapsed_seconds=2.0, version_tag="v2026.10.28.1")
    return run_id


def _seed_vectors(engine) -> None:
    rows = [
        ("v1", "phrases", json.dumps({"source": "phrases", "source_id": 1}), "2026-10-01T00:00:00+00:00"),
        ("v2", "phrases", json.dumps({"source": "phrases", "source_id": 99}), "2026-10-02T00:00:00+00:00"),
        ("v3", "phrases", json.dumps({"source": "phrases"}), "2026-10-03T00:00:00+00:00"),
        ("v4", "wiki_content", json.dumps({"source": "wiki_content", "source_id": 7}), None),
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO knowledge_vectors (id, source, metadata, imported_at) "
                "VALUES (:i, :s, :m, :t)"
            ),
            [{"i": i, "s": s, "m": m, "t": t} for i, s, m, t in rows],
        )


# ── classification ──────────────────────────────────────────────────────────


def test_every_spec_is_admin_only_and_read_only() -> None:
    assert set(LEARNING_RUN_SPECS) == set(LEARNING_RUN_TOOL_NAMES) == set(LEARNING_RUN_HANDLERS)
    for name, spec in LEARNING_RUN_SPECS.items():
        assert spec.admin_only is True, name
        assert spec.writes is False, name
        assert spec.destructive is False, name
        assert spec.requires_network is False, name
        assert spec.scope in {"pipeline:read", "eval:read", "rag:read"}, name
        assert get_tool(name) is spec


def test_the_ledger_tools_are_absent_below_admin() -> None:
    member = allow_list("member")
    public = allow_list("public")
    for name in LEARNING_RUN_TOOL_NAMES:
        assert name not in member, name
        assert name not in public, name
        assert name in allow_list("admin"), name


def test_handlers_cover_the_specs() -> None:
    from zolai.agent.tools.executor import HANDLERS

    for name in LEARNING_RUN_TOOL_NAMES:
        assert HANDLERS[name] is LEARNING_RUN_HANDLERS[name]


# ── learning_run_status ─────────────────────────────────────────────────────


def test_run_status_is_empty_before_anything_ran(engine) -> None:
    out = LEARNING_RUN_HANDLERS["learning_run_status"]({})
    assert out["run"] is None
    assert out["runs"] == []
    assert out["count"] == 0
    assert "no learning run" in out["note"].lower()


def test_run_status_returns_the_run_without_paths_or_secrets(engine) -> None:
    run_id = _seed_run(engine)

    out = LEARNING_RUN_HANDLERS["learning_run_status"]({"run_id": run_id})

    assert out["run"]["run_id"] == run_id
    assert out["run"]["status"] == "SUCCEEDED"
    assert out["run"]["version_tag"] == "v2026.10.28.1"
    assert out["run"]["steps_total"] == 1
    assert out["run"]["steps"][0]["name"] == "train_pos"

    payload = json.dumps(out)
    assert "secret-place" not in payload
    assert "<path>" in payload
    assert _FAKE_SECRET not in payload
    assert "<redacted>" in payload
    assert "options" not in json.dumps(out["run"])
    assert "staging_ref" not in payload
    assert "published_ref" not in payload
    assert "error_message" not in payload


def test_run_status_history_omits_steps(engine) -> None:
    _seed_run(engine)

    out = LEARNING_RUN_HANDLERS["learning_run_status"]({"limit": 1})

    assert out["count"] == 1
    assert out["run"]["run_id"] == out["runs"][0]["run_id"]
    assert "steps" not in out["runs"][0]


def test_run_status_rejects_an_unknown_or_malformed_id(engine) -> None:
    with pytest.raises(ToolError) as unknown:
        LEARNING_RUN_HANDLERS["learning_run_status"]({"run_id": "lr_missing"})
    assert unknown.value.code is ToolErrorCode.NOT_FOUND

    with pytest.raises(ToolError) as malformed:
        LEARNING_RUN_HANDLERS["learning_run_status"]({"run_id": "../etc/passwd"})
    assert malformed.value.code is ToolErrorCode.INVALID_ARGUMENT


# ── learning_version_history ────────────────────────────────────────────────


def _insert_version(engine, *, version: str, run_id: str) -> None:
    sources = {"learning_run": run_id, "mode": "apply", "git_commit": "abc1234"}
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO knowledge_versions "
                "(version, git_commit, source_versions, manifest_hash, status) "
                "VALUES (:v, 'abc1234', :s, :h, 'VERIFIED')"
            ),
            {"v": version, "s": json.dumps(sources), "h": "f" * 64},
        )


def test_version_history_lists_tags_newest_first(engine) -> None:
    run_id = _seed_run(engine)
    _insert_version(engine, version="v2026.10.27.1", run_id=run_id)
    _insert_version(engine, version="v2026.10.28.1", run_id=run_id)

    out = LEARNING_RUN_HANDLERS["learning_version_history"]({})

    assert [v["version"] for v in out["versions"]] == ["v2026.10.28.1", "v2026.10.27.1"]
    assert out["count"] == 2
    first = out["versions"][0]
    assert first["status"] == "VERIFIED"
    assert first["manifest_hash"] == "f" * 64
    assert first["learning_run"] == run_id
    assert "run" not in first


def test_version_history_can_attach_the_run_status(engine) -> None:
    run_id = _seed_run(engine)
    _insert_version(engine, version="v2026.10.28.1", run_id=run_id)

    out = LEARNING_RUN_HANDLERS["learning_version_history"]({"include_runs": True})

    run = out["versions"][0]["run"]
    assert run["run_id"] == run_id
    assert run["status"] == "SUCCEEDED"
    assert run["steps_total"] == 1


def test_version_history_respects_the_limit(engine) -> None:
    run_id = _seed_run(engine)
    for day in range(1, 6):
        _insert_version(engine, version=f"v2026.10.0{day}.1", run_id=run_id)

    out = LEARNING_RUN_HANDLERS["learning_version_history"]({"limit": 2})

    assert out["count"] == 2


# ── learning_rag_status ─────────────────────────────────────────────────────


def test_rag_status_counts_vectors_and_unattributable_rows(engine) -> None:
    _seed_vectors(engine)

    out = LEARNING_RUN_HANDLERS["learning_rag_status"]({"sources": ["phrases"]})

    assert out["count"] == 1
    entry = out["sources"][0]
    assert entry["source"] == "phrases"
    assert entry["vectors"] == 3
    assert entry["unattributable_rows"] == 1
    assert entry["indexed"] is True
    assert entry["watermark"] == "2026-10-03T00:00:00+00:00"
    assert "stale_source_ids" not in entry


def test_rag_status_counts_stale_source_ids_without_deleting(engine) -> None:
    _seed_vectors(engine)

    out = LEARNING_RUN_HANDLERS["learning_rag_status"](
        {"sources": ["phrases"], "check_stale": True}
    )

    entry = out["sources"][0]
    assert entry["stale_source_ids"] == 1  # id 99 has vectors but no row
    assert entry["stale_checked"] is True
    with engine.connect() as conn:
        remaining = conn.execute(text("SELECT COUNT(*) FROM knowledge_vectors")).scalar()
    assert remaining == 4, "a status read must never prune"


def test_rag_status_never_reports_a_skipped_check_as_zero(engine) -> None:
    # No source table at all: the stale check is *not* attempted.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE phrases"))
        conn.execute(
            text(
                "INSERT INTO knowledge_vectors (id, source, metadata) "
                "VALUES ('x', 'bible_verses', '{\"source_id\": 5}')"
            )
        )

    out = LEARNING_RUN_HANDLERS["learning_rag_status"](
        {"sources": ["bible_verses"], "check_stale": True}
    )

    entry = out["sources"][0]
    assert entry["vectors"] == 1
    assert entry["stale_source_ids"] is None
    assert entry["stale_checked"] is False


def test_rag_status_rejects_an_unknown_source(engine) -> None:
    with pytest.raises(ToolError) as excinfo:
        LEARNING_RUN_HANDLERS["learning_rag_status"]({"sources": ["not_a_source"]})
    assert excinfo.value.code is ToolErrorCode.INVALID_ARGUMENT
    assert "not_a_source" in str(excinfo.value)


def test_rag_status_is_honest_on_an_empty_database(tmp_path, monkeypatch) -> None:
    bare = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    monkeypatch.setattr(learning_runs, "_engine", lambda: bare)

    out = LEARNING_RUN_HANDLERS["learning_rag_status"]({})

    assert out["sources"] == []
    assert out["total_vectors"] == 0
    assert "knowledge_vectors" in out["note"]
