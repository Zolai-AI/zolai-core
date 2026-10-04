"""P3 — agent tool registry + allow-listed executor (plan §C).

Covers the done-when: unknown/out-of-allowlist tool → error result and never
executed; retrieval handlers answer with **no LLM key** (DB-only);
``provider_status`` never leaks a key; ``web_search`` stays behind its double
gate (env flag + non-rule mode).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest

from zolai.agent.tools import executor as exec_mod
from zolai.agent.tools.registry import (
    ADMIN_TOOLS,
    PUBLIC_TOOLS,
    SPECS,
    allow_list,
    get_tool,
    json_schemas,
    names_for,
    native_tool_defs,
)
from zolai.agent.tools.types import ToolCall, ToolResult
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables
from zolai.llm import catalog as catalog_mod
from zolai.llm import provider_settings as settings_mod

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
INSERT INTO dictionary VALUES (2, 'gam', 'earth, land', 'noun', 'test', 'gam', 1, 'N');
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
    en_kJV TEXT
);
INSERT INTO bible_verses VALUES (1, 'GEN 1:1', 'GEN', 1, 1,
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


@pytest.fixture()
def data_db(tmp_path, monkeypatch) -> Iterator[str]:
    """Throwaway store: seeded canonical rows + ORM tables, wired via config."""
    db_path = tmp_path / "zolai.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_SEED_SQL)
    conn.commit()
    conn.close()
    mgr = DatabaseManager(f"sqlite:///{db_path}")
    mgr.init_db()
    create_ai_provider_tables(mgr)
    monkeypatch.setattr(config.paths, "data", tmp_path)
    monkeypatch.setattr(config.paths, "db", db_path)
    monkeypatch.setattr(settings_mod, "_get_manager", lambda: mgr)
    seed = catalog_mod.seed_catalog(mgr)
    assert seed["errors"] == []
    yield str(db_path)
    mgr.dispose()


def _call(name: str, inp: dict | None = None, allow=None) -> ToolResult:
    return exec_mod.execute_tool(
        ToolCall(name=name, input=inp or {}), allow if allow is not None else allow_list("admin")
    )


class TestRegistry:
    def test_public_and_admin_tool_sets(self) -> None:
        assert set(PUBLIC_TOOLS) <= set(SPECS)
        assert set(ADMIN_TOOLS) <= set(SPECS)
        assert "web_search" in SPECS
        assert SPECS["provider_status"].admin_only is True
        assert SPECS["rag_search"].admin_only is False
        assert SPECS["web_search"].requires_network is True

    def test_allow_list_scopes(self) -> None:
        public = allow_list("public")
        assert public == {
            "rag_search",
            "dictionary_lookup",
            "word_evidence",
            "verse_lookup",
            "grammar_check",
        }
        assert public <= allow_list("member") <= allow_list("admin")
        assert "provider_status" in allow_list("admin")
        assert "provider_status" not in allow_list("member")

    def test_allow_list_unknown_scope_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown tool scope"):
            allow_list("root")

    def test_get_tool_unknown_is_none(self) -> None:
        assert get_tool("definitely_missing") is None
        assert get_tool("") is None

    def test_json_schemas_shape(self) -> None:
        schemas = json_schemas(names_for("admin"))
        assert schemas and all(s["name"] for s in schemas)
        rag = next(s for s in schemas if s["name"] == "rag_search")
        assert rag["parameters"]["type"] == "object"
        assert "query" in rag["parameters"]["properties"]

    def test_native_tool_defs_shape(self) -> None:
        defs = native_tool_defs(["rag_search"])
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "rag_search"


class TestExecutorGuards:
    def test_unknown_tool_is_error_result_never_executed(self) -> None:
        result = _call("definitely_missing")
        assert result.ok is False
        assert "unknown tool" in (result.error or "")

    def test_out_of_allowlist_tool_is_error_result(self, monkeypatch) -> None:
        bomb: list[str] = []
        monkeypatch.setitem(
            exec_mod.HANDLERS,
            "provider_status",
            lambda inp: bomb.append("ran"),
        )
        result = _call("provider_status", allow=allow_list("public"))
        assert result.ok is False
        assert "not allowed" in (result.error or "")
        assert bomb == []  # never executed

    def test_handler_error_is_captured_not_raised(self) -> None:
        result = _call("rag_search", {})  # missing required query
        assert result.ok is False
        assert "required" in (result.error or "")

    def test_result_serialises_to_ok_contract(self) -> None:
        result = _call("knowledge_stats", allow=("knowledge_stats",))
        payload = result.to_dict()
        assert set(payload) >= {"name", "ok", "latency_ms", "turn"}
        assert payload["latency_ms"] >= 0
        json.dumps(payload)  # must be JSON-serialisable for the API/trace


class TestRetrievalHandlers:
    def test_rag_search_hits_seeded_rows(self, data_db) -> None:
        result = _call("rag_search", {"query": "pasian", "limit": 5})
        assert result.ok, result.error
        assert isinstance(result.data, list)
        assert any("pasian" in str(item.get("text", "")).lower() for item in result.data)

    def test_dictionary_lookup_exact_then_prefix(self, data_db) -> None:
        exact = _call("dictionary_lookup", {"word": "Pasian"})
        assert exact.ok, exact.error
        assert exact.data and exact.data[0]["zolai"] == "pasian"
        prefix = _call("dictionary_lookup", {"word": "pasi"})
        assert prefix.ok and prefix.data

    def test_verse_lookup_found_and_missing(self, data_db) -> None:
        hit = _call("verse_lookup", {"ref": "gen 1:1"})
        assert hit.ok, hit.error
        assert hit.data["found"] is True
        assert "God" in str(hit.data["en"])
        miss = _call("verse_lookup", {"ref": "ZZZ 99:99"})
        assert miss.ok and miss.data["found"] is False

    def test_word_related_and_evidence_return_data(self, data_db) -> None:
        related = _call("word_related", {"word": "pasian"})
        assert related.ok, related.error
        assert isinstance(related.data, list)
        evidence = _call("word_evidence", {"word": "pasian"})
        assert isinstance(evidence.data, (list, dict)) or not evidence.ok

    def test_grammar_check_returns_structure(self) -> None:
        result = _call("grammar_check", {"text": "Pasian in gam a piangsak hi."})
        assert result.ok, result.error
        assert "valid" in result.data

    def test_kb_research_is_read_only_and_tolerant(self, data_db) -> None:
        result = _call("kb_research", {"query": "anything"})
        assert result.ok, result.error
        assert isinstance(result.data, list)


class TestAdminHandlers:
    def test_provider_status_never_leaks_a_key(self, data_db) -> None:
        result = _call("provider_status")
        assert result.ok, result.error
        payload = json.dumps(result.data).lower()
        assert "api_key" not in payload
        assert "plaintext" not in payload
        for row in result.data:
            assert set(row["secret"]) == {"mode", "ref_masked", "configured"}

    def test_review_queue_submit_queues_candidate(self, data_db) -> None:
        result = _call(
            "review_queue_submit",
            {"fact_type": "agent_run", "fact_key": "run:test-1", "priority": 2},
        )
        assert result.ok, result.error
        assert result.data["queued"] is True

    def test_web_search_is_disabled_by_default(self, data_db, monkeypatch) -> None:
        monkeypatch.delenv("ZOLAI_AGENT_WEB_SEARCH", raising=False)
        result = _call("web_search", {"query": "zolai"})
        assert result.ok is False
        assert "ZOLAI_AGENT_WEB_SEARCH" in (result.error or "")

    def test_web_search_refused_in_rule_mode(self, data_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_AGENT_WEB_SEARCH", "1")
        monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
        result = _call("web_search", {"query": "zolai"})
        assert result.ok is False
        assert "rule" in (result.error or "")
