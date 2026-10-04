"""P3 — agent runs: marker loop, API surface, learn phase (plan §C).

Covers the done-when:

- ``zolai agent run``-equivalent (via API) completes offline in ``rule`` mode
  with phases + tool_calls + evidence + provider/model/turns/latency on the row;
- the marker loop parses fenced ``<<<TOOL>>>`` blocks, respects ≤1 tool/turn
  and ``AGENT_MAX_TURNS``, and strips markers from the reply;
- unknown / out-of-allowlist tools → error result (never executed);
- thumbs-up creates a ``hypotheses`` + review-queue candidate and **no
  canonical table row** (source scan + row-count assertion);
- strict auth: anon 401 (warn + enforce), missing scope 403, real 404s,
  in-process 5 runs/min → 429.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zolai.agent import loop as loop_mod
from zolai.agent.loop import (
    HOLD_CHARS,
    TOOL_MARKER,
    TOOL_RESULT_MARKER,
    TokenGate,
    parse_tool_calls,
    run_agent_loop,
)
from zolai.agent.tools.registry import allow_list
from zolai.api import auth
from zolai.api.agent_router import _reset_run_bucket
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import (
    create_ai_provider_tables,
    create_api_keys_table,
    create_knowledge_tables,
)

_SEED_SQL = """
CREATE TABLE dictionary (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT,
    pos TEXT,
    source TEXT,
    syllables TEXT,
    syllable_count INTEGER,
    pos_canonical TEXT
);
INSERT INTO dictionary VALUES (1, 'pasian', 'God', 'noun', 'test', 'pa|sian', 2, 'N');
CREATE TABLE dictionary_en_zo (
    id INTEGER PRIMARY KEY,
    headword TEXT NOT NULL,
    translations TEXT,
    pos TEXT,
    source TEXT
);
INSERT INTO dictionary_en_zo VALUES (1, 'god', 'pasian', 'noun', 'test');
CREATE TABLE bible_verses (
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    book TEXT,
    chapter INTEGER,
    verse INTEGER,
    zo_tdb77 TEXT,
    zo_tedim2010 TEXT,
    en_kJV TEXT
);
INSERT INTO bible_verses (id, ref, book, chapter, verse, zo_tdb77, en_kJV) VALUES (
    1, 'GEN 1:1', 'GEN', 1, 1,
    'Pasian in vantung leh leitung a piangsak hi.',
    'In the beginning God created the heaven and the earth.');
CREATE TABLE phrases (
    id INTEGER PRIMARY KEY,
    zolai TEXT NOT NULL,
    english TEXT,
    frequency INTEGER
);
INSERT INTO phrases VALUES (1, 'pasian in', 'God (ergative)', 10);
"""

#: Canonical tables the learn phase must never touch (row counts checked).
_CANONICAL_TABLES = ("dictionary", "dictionary_en_zo", "bible_verses", "phrases")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    _reset_run_bucket()


@pytest.fixture()
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Seeded throwaway store wired in via config + provider singleton."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    create_knowledge_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    yield db_path
    mgr.dispose()


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'agent_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def runner_key() -> dict:
    return auth.create_api_key(
        name="agent-runner", scopes=["agent:run", "agent:read"], created_by="test"
    )


@pytest.fixture()
def reader_key() -> dict:
    return auth.create_api_key(name="agent-reader", scopes=["agent:read"], created_by="test")


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="agent-member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


# ---------------------------------------------------------------------------
# Marker protocol (loop unit tests — no sockets, injected chat)
# ---------------------------------------------------------------------------


class TestMarkerProtocol:
    def test_parse_plain_marker_block(self) -> None:
        text = f'Running...\n{TOOL_MARKER}\n{{"name": "rag_search", "input": {{"query": "pasian"}}}}\ndone'
        parsed = parse_tool_calls(text)
        assert [c.name for c in parsed.calls] == ["rag_search"]
        assert parsed.calls[0].input == {"query": "pasian"}
        assert "Running..." in parsed.text and "done" in parsed.text
        assert TOOL_MARKER not in parsed.text

    def test_parse_fenced_block_is_tolerant(self) -> None:
        payload = '{"name": "dictionary_lookup", "input": {"word": "pasian", "nested": {"a": 1}}}'
        text = f"{TOOL_MARKER}\n```json\n{payload}\n```"
        parsed = parse_tool_calls(text)
        assert [c.name for c in parsed.calls] == ["dictionary_lookup"]
        assert parsed.calls[0].input["nested"] == {"a": 1}
        assert "```" not in parsed.text

    def test_broken_payload_is_stripped_never_leaked(self) -> None:
        text = f"hello {TOOL_MARKER} {{not json}} world"
        parsed = parse_tool_calls(text)
        assert parsed.calls == []
        assert TOOL_MARKER not in parsed.text
        assert "hello" in parsed.text and "world" in parsed.text

    def test_marker_without_payload_is_stripped(self) -> None:
        parsed = parse_tool_calls(f"answer {TOOL_MARKER} tail")
        assert parsed.calls == []
        assert TOOL_MARKER not in parsed.text

    def test_tools_section_embeds_json_schema(self) -> None:
        section = loop_mod.build_tools_section(allow_list("public"))
        assert section.startswith("## TOOLS")
        assert '"rag_search"' in section
        assert TOOL_MARKER in section
        assert "at most ONE tool" in section

    def test_token_gate_holds_and_drops_marker_prefix(self) -> None:
        out: list[str] = []
        gate = TokenGate(out.append)
        for token in ["Hel", "lo wor", "ld", TOOL_MARKER[:6]]:
            gate.push(token)
        # clean text is kept, the trailing partial marker never reaches the sink
        assert gate.end_turn() == "dropped"
        assert "".join(out) == "Hello world"
        assert TOOL_MARKER[:6] not in "".join(out)

    def test_token_gate_flushes_clean_turn_after_hold_chars(self) -> None:
        out: list[str] = []
        gate = TokenGate(out.append)
        gate.push("a")  # below HOLD_CHARS — held back
        assert out == []
        gate.push("bcdefghijklmnopqrstuvwxyz")
        gate.end_turn()
        assert "".join(out) == "abcdefghijklmnopqrstuvwxyz"
        assert HOLD_CHARS == 16


class TestLoopBehaviour:
    def test_one_tool_per_turn_and_followup_keeps_question(self) -> None:
        two_markers = (
            f'{TOOL_MARKER}\n{{"name": "rag_search", "input": {{"query": "a"}}}}\n'
            f'{TOOL_MARKER}\n{{"name": "dictionary_lookup", "input": {{"word": "b"}}}}'
        )
        responses = [
            {"text": two_markers, "tool_calls": []},
            {"text": "Final answer from evidence.", "tool_calls": []},
        ]
        seen_users: list[str] = []

        def chat(messages, tools=None):
            seen_users.append(messages[-1]["content"])
            return responses.pop(0)

        result = run_agent_loop(
            system_prompt="sys",
            user_message="What is pasian?",
            allow=allow_list("public"),
            chat=chat,
        )
        assert result["ok"] is True
        assert result["turns"] == 2
        # exactly one tool executed despite two markers in the turn
        executed = [t for t in result["tool_calls"] if t.get("status")]
        assert len(executed) == 1
        assert executed[0]["name"] == "rag_search"
        assert executed[0]["input"] == {"query": "a"}
        assert result["reply"] == "Final answer from evidence."
        assert TOOL_MARKER not in result["reply"]
        # follow-up turn keeps the original question alongside the result
        assert seen_users[1].startswith("What is pasian?")
        assert TOOL_RESULT_MARKER in seen_users[1]

    def test_max_turns_enforced_with_real_fallback(self) -> None:
        always_tool = {
            "text": f'{TOOL_MARKER}\n{{"name": "rag_search", "input": {{"query": "x"}}}}',
            "tool_calls": [],
        }
        state = {"n": 0}

        def chat(messages, tools=None):
            state["n"] += 1
            return dict(always_tool)

        result = run_agent_loop(
            system_prompt="sys",
            user_message="goal",
            allow=allow_list("public"),
            chat=chat,
            max_turns=3,
        )
        assert state["n"] == 3
        assert result["turns"] == 3
        assert result["ok"] is False
        assert result["error"] == loop_mod.MAX_TURNS_EXCEEDED
        assert len(result["tool_calls"]) == 3
        assert TOOL_MARKER not in result["reply"]

    def test_out_of_allowlist_tool_errors_without_execution(self) -> None:
        responses = [
            {
                "text": f'{TOOL_MARKER}\n{{"name": "provider_status", "input": {{}}}}',
                "tool_calls": [],
            },
            {"text": "I could not use that tool.", "tool_calls": []},
        ]
        result = run_agent_loop(
            system_prompt="sys",
            user_message="goal",
            allow=allow_list("public"),
            chat=lambda m, t=None: responses.pop(0),
        )
        assert result["tool_calls"][0]["ok"] is False
        assert "not allowed" in result["tool_calls"][0]["error"]
        assert result["reply"] == "I could not use that tool."

    def test_native_tools_offered_turn1_only_and_never_for_brain_flag(self) -> None:
        seen: list = []
        responses = [
            {"text": f'{TOOL_MARKER}\n{{"name": "rag_search", "input": {{"query": "x"}}}}'},
            {"text": "done"},
        ]

        def chat(messages, tools=None):
            seen.append(tools)
            return responses.pop(0)

        run_agent_loop(
            system_prompt="sys",
            user_message="goal",
            allow=allow_list("public"),
            chat=chat,
            native=True,
        )
        assert seen[0] is not None
        assert len(seen[0]) == len(allow_list("public"))  # one function schema per tool
        assert all(t.get("type") == "function" for t in seen[0])
        assert seen[1] is None  # follow-up rides the prompt protocol

        seen.clear()
        responses = [
            {"text": f'{TOOL_MARKER}\n{{"name": "rag_search", "input": {{"query": "x"}}}}'},
            {"text": "done"},
        ]
        run_agent_loop(
            system_prompt="sys",
            user_message="goal",
            allow=allow_list("public"),
            chat=chat,
            native=False,  # brain: never a tools key
        )
        assert all(t is None for t in seen)

    def test_provider_exception_is_captured_not_raised(self) -> None:
        def chat(messages, tools=None):
            raise RuntimeError("boom")

        result = run_agent_loop(
            system_prompt="sys",
            user_message="goal",
            allow=allow_list("public"),
            chat=chat,
        )
        assert result["ok"] is False
        assert "provider_error" in result["error"]


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


class TestAgentRunsApi:
    def test_rule_mode_run_completes_offline(
        self, client, data_db, auth_db, runner_key, monkeypatch
    ) -> None:
        from test_engine_contract import network_blocked

        monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
        with network_blocked() as attempts:
            resp = client.post(
                "/api/v1/agent/runs",
                json={"goal": "summarize what pasian means"},
                headers=_headers(runner_key),
            )
        assert attempts == [], f"rule-mode run attempted network: {attempts}"
        assert resp.status_code == 200, resp.text
        run = resp.json()
        assert run["status"] == "succeeded"
        assert run["mode"] == "rule"
        assert set(run["phases"]) >= {"research", "build", "review", "shipped"}
        assert run["phases"]["review"]["valid"] is True
        assert isinstance(run["tool_calls"], list) and run["tool_calls"]
        assert isinstance(run["evidence"], list)
        assert run["answer"]
        assert run["turns"] == 0  # rule mode never calls a model
        assert run["provider"] == "" and run["model"] == ""
        assert run["latency_ms"] > 0
        assert run["outcome"] == "rule_draft"

    def test_anonymous_is_401_strict_in_warn_and_enforce(
        self, client, data_db, auth_db, monkeypatch
    ) -> None:
        for mode in ("warn", "enforce"):
            monkeypatch.setenv("ZOLAI_API_AUTH", mode)
            resp = client.post("/api/v1/agent/runs", json={"goal": "x"})
            assert resp.status_code == 401, (mode, resp.status_code)

    def test_member_without_agent_scope_is_403(self, client, data_db, auth_db, member_key) -> None:
        resp = client.post(
            "/api/v1/agent/runs", json={"goal": "x"}, headers=_headers(member_key)
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["reason"] == "missing_scope"

    def test_reader_key_can_list_but_not_run(
        self, client, data_db, auth_db, reader_key
    ) -> None:
        assert client.get("/api/v1/agent/runs", headers=_headers(reader_key)).status_code == 200
        assert (
            client.post(
                "/api/v1/agent/runs", json={"goal": "x"}, headers=_headers(reader_key)
            ).status_code
            == 403
        )

    def test_unknown_run_is_real_404(self, client, data_db, auth_db, runner_key) -> None:
        resp = client.get("/api/v1/agent/runs/999999", headers=_headers(runner_key))
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "run_not_found"

    def test_health_reports_mode_and_budgets(self, client, data_db, auth_db, reader_key) -> None:
        resp = client.get("/api/v1/agent/health", headers=_headers(reader_key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["mode"] == "rule"
        assert body["max_turns"] >= 1

    def test_run_rate_limit_is_5_per_minute(self, client, data_db, auth_db, runner_key) -> None:
        statuses = []
        for i in range(6):
            resp = client.post(
                "/api/v1/agent/runs",
                json={"goal": f"goal {i}"},
                headers=_headers(runner_key),
            )
            statuses.append(resp.status_code)
        assert statuses[:5] == [200] * 5
        assert statuses[5] == 429

    def test_empty_goal_is_rejected_by_schema(self, client, data_db, auth_db, runner_key) -> None:
        resp = client.post(
            "/api/v1/agent/runs", json={"goal": ""}, headers=_headers(runner_key)
        )
        assert resp.status_code == 422  # pydantic min_length=1


class TestFeedbackAndLearn:
    def test_thumbs_up_writes_proposals_and_no_canonical_rows(
        self, client, data_db, auth_db, runner_key
    ) -> None:
        created = client.post(
            "/api/v1/agent/runs",
            json={"goal": "what does pasian mean?"},
            headers=_headers(runner_key),
        )
        assert created.status_code == 200
        run_id = created.json()["id"]

        before = _canonical_counts(data_db)
        resp = client.post(
            f"/api/v1/agent/runs/{run_id}/feedback",
            json={"score": 1},
            headers=_headers(runner_key),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["feedback_score"] == 1
        assert body["learn"]["learned"] is True
        assert body["learn"]["hypothesis_id"]
        assert body["learn"]["review_item_id"]
        after = _canonical_counts(data_db)
        assert before == after, "learn must never write a canonical table"

        # proposal rows really exist
        import sqlite3

        conn = sqlite3.connect(data_db)
        hyp = conn.execute(
            "SELECT kind, subject FROM hypotheses WHERE kind='agent' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        queue = conn.execute(
            "SELECT status FROM foundation_review_queue ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert hyp is not None and hyp[1].startswith("agent_run:")
        assert queue is not None and queue[0] == "pending"

    def test_thumbs_down_skips_learn(self, client, data_db, auth_db, runner_key) -> None:
        created = client.post(
            "/api/v1/agent/runs", json={"goal": "goal"}, headers=_headers(runner_key)
        )
        run_id = created.json()["id"]
        resp = client.post(
            f"/api/v1/agent/runs/{run_id}/feedback",
            json={"score": -1},
            headers=_headers(runner_key),
        )
        assert resp.status_code == 200
        assert resp.json()["learn"]["learned"] is False

    def test_feedback_on_unknown_run_is_404(self, client, data_db, auth_db, runner_key) -> None:
        resp = client.post(
            "/api/v1/agent/runs/424242/feedback",
            json={"score": 1},
            headers=_headers(runner_key),
        )
        assert resp.status_code == 404


class TestLearnIsProposalOnly:
    def test_agent_package_has_no_raw_writes_outside_agent_runs(self) -> None:
        """Source scan (plan §C risk): no INSERT/UPDATE/DELETE beyond agent_runs."""
        root = Path(__file__).resolve().parents[1] / "zolai" / "agent"
        pattern = re.compile(
            r"\b(?:INSERT\s+INTO|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)"
            r"|\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)\s+SET",
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in sorted(root.rglob("*.py")):
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                table = (match.group(1) or match.group(2)).lower()
                if table != "agent_runs":
                    violations.append(f"{path.name}: {match.group(0)}")
        assert violations == [], f"agent package writes outside agent_runs: {violations}"

    def test_learn_module_only_names_proposal_tables(self) -> None:
        from zolai.agent import learn

        assert learn.PROPOSAL_TABLES == ("hypotheses", "foundation_review_queue")
        assert learn.ALLOWED_SQL_WRITE_TABLES == frozenset({"agent_runs"})


def _canonical_counts(db_path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in _CANONICAL_TABLES
        }
    finally:
        conn.close()
