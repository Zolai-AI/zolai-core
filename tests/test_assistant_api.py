"""P4 — assistants: public chat (anonymous, enforce-safe) + strict admin chat.

Covers the done-when:

- ``POST /api/v1/assistant/chat`` answers anonymously in **warn and enforce**
  (listed in ``rbac.PUBLIC_ROUTES``) with ``retrieval_only: true`` and
  ``citations`` — honest fallback, never fake generation;
- the public route never persists (``persist`` is admin-only);
- ``POST /api/v1/admin/assistant/chat`` is strict: anon 401, member 403,
  admin key with ``agent:run`` → 200 + tool-call trace, ``persist: true``
  writes an ``agent_runs`` row;
- both routes return the ZVS review block and never leak a marker.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_agent_runs import _SEED_SQL

from zolai.api import assistant_router, auth, providers_router, rbac
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app
from zolai.config import config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_ai_provider_tables, create_api_keys_table


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def data_db(tmp_path, monkeypatch) -> Iterator[Path]:
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
    monkeypatch.setenv("ZOLAI_ENGINE_MODE", "rule")
    yield db_path
    mgr.dispose()


@pytest.fixture()
def auth_db(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'assistant_auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    monkeypatch.setattr(auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def admin_key() -> dict:
    return auth.create_api_key(name="assistant-admin", scopes=["*"], created_by="test")


@pytest.fixture()
def member_key() -> dict:
    return auth.create_api_key(name="assistant-member", scopes=["dataset:read"], created_by="test")


def _headers(key: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {key['plaintext']}"}


class TestPublicAssistant:
    @pytest.mark.parametrize("mode", ["warn", "enforce"])
    def test_anonymous_chat_open_in_every_mode(
        self, client, data_db, auth_db, monkeypatch, mode
    ) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", mode)
        resp = client.post("/api/v1/assistant/chat", json={"message": "pasian"})
        assert resp.status_code == 200, (mode, resp.text)
        body = resp.json()
        assert body["retrieval_only"] is True  # rule mode, no key → honest fallback
        assert body["mode"] == "rule"
        assert isinstance(body["citations"], list) and body["citations"]
        assert body["tool_calls"]
        assert body["answer"].strip()
        assert "<<<TOOL" not in body["answer"]
        assert set(body["zvs"]) == {"valid", "violations_before", "violations_after"}

    def test_public_route_is_declared_public(self) -> None:
        assert ("POST", "/api/v1/assistant/chat") in rbac.PUBLIC_ROUTES

    def test_forged_forwarded_for_cannot_bypass_chat_rate_limit(self, client, data_db, auth_db, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        monkeypatch.setenv("ZOLAI_PUBLIC_CHAT_RATE_LIMIT_RPM", "2")
        monkeypatch.setattr(assistant_router, "llm_allowed", lambda: True)
        monkeypatch.setattr(
            assistant_router,
            "make_chat_fn",
            lambda **kwargs: (
                lambda messages: "unused",
                {"native": False, "catalog_id": "test", "model": "test-model"},
            ),
        )
        monkeypatch.setattr(providers_router, "validate_selection", lambda *args, **kwargs: None)
        provider_calls = 0

        def fake_run_agent_loop(**_kwargs: Any) -> dict[str, Any]:
            nonlocal provider_calls
            provider_calls += 1
            return {"reply": "Pasian in leitung a bawl hi.", "tool_calls": [], "turns": 1}

        monkeypatch.setattr(assistant_router, "run_agent_loop", fake_run_agent_loop)

        for forwarded_for in ("198.51.100.1", "198.51.100.2"):
            response = client.post(
                "/api/v1/assistant/chat",
                headers={"X-Forwarded-For": forwarded_for},
                json={"message": "pasian"},
            )
            assert response.status_code == 200, response.text

        blocked = client.post(
            "/api/v1/assistant/chat",
            headers={"X-Forwarded-For": "198.51.100.3"},
            json={"message": "pasian"},
        )

        assert blocked.status_code == 429
        assert blocked.json()["detail"]["scope"] == "public_chat"
        assert provider_calls == 2

    def test_public_never_persists(self, client, data_db, auth_db) -> None:
        resp = client.post(
            "/api/v1/assistant/chat", json={"message": "define gam", "persist": True}
        )
        assert resp.status_code == 200
        assert "persisted_run_id" not in resp.json()
        conn = sqlite3.connect(data_db)
        # the store table may exist (created lazily) but must hold no rows
        try:
            count = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
        except sqlite3.OperationalError:
            count = 0
        finally:
            conn.close()
        assert count == 0

    def test_empty_message_rejected(self, client, data_db, auth_db) -> None:
        resp = client.post("/api/v1/assistant/chat", json={"message": ""})
        assert resp.status_code == 422


class TestAdminAssistant:
    def test_anonymous_is_401_in_warn_and_enforce(
        self, client, data_db, auth_db, monkeypatch
    ) -> None:
        for mode in ("warn", "enforce"):
            monkeypatch.setenv("ZOLAI_API_AUTH", mode)
            resp = client.post(
                "/api/v1/admin/assistant/chat", json={"message": "hello"}
            )
            assert resp.status_code == 401, (mode, resp.status_code)

    def test_member_key_is_403(self, client, data_db, auth_db, member_key) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "hello"},
            headers=_headers(member_key),
        )
        assert resp.status_code == 403

    def test_admin_key_chat_returns_trace(
        self, client, data_db, auth_db, admin_key
    ) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "pasian"},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retrieval_only"] is True
        assert isinstance(body["tool_calls"], list) and body["tool_calls"]
        assert body["citations"]
        assert "<<<TOOL" not in body["answer"]
        assert set(body["zvs"]) == {"valid", "violations_before", "violations_after"}

    def test_admin_persist_writes_agent_run(
        self, client, data_db, auth_db, admin_key
    ) -> None:
        resp = client.post(
            "/api/v1/admin/assistant/chat",
            json={"message": "define pasian", "persist": True},
            headers=_headers(admin_key),
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["persisted_run_id"]
        assert isinstance(run_id, int)
        conn = sqlite3.connect(data_db)
        row = conn.execute(
            "SELECT status, outcome, created_by FROM agent_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        conn.close()
        assert row == ("succeeded", "chat", "admin-chat")


class TestAssistantFeedback:
    def test_feedback_endpoint_is_public(self) -> None:
        assert ("POST", "/api/v1/assistant/feedback") in rbac.PUBLIC_ROUTES

    def test_feedback_records_successfully(self, client, data_db) -> None:
        resp = client.post(
            "/api/v1/assistant/feedback",
            json={
                "message": "What is pasian?",
                "response": "Pesian is God.",
                "mode": "public",
                "retrieval_only": True,
                "provider": "",
                "model": "",
                "score": 1,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify data persisted
        conn = sqlite3.connect(data_db)
        row = conn.execute(
            "SELECT message, response, score FROM assistant_chat_feedback"
        ).fetchone()
        conn.close()
        assert row == ("What is pasian?", "Pesian is God.", 1)

    def test_negative_feedback_records(self, client, data_db) -> None:
        resp = client.post(
            "/api/v1/assistant/feedback",
            json={
                "message": "What is gam?",
                "response": "Gam is place.",
                "mode": "public",
                "retrieval_only": True,
                "provider": "",
                "model": "",
                "score": -1,
            },
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(data_db)
        row = conn.execute(
            "SELECT score FROM assistant_chat_feedback ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row == (-1,)


class TestMultiWordDictionaryLookup:
    """A question made of several words must look up each of them.

    Only the *first* word was looked up, so for "nang bang hih thei" the model
    received one dictionary entry and reported no meaning for the other three —
    correctly, given the evidence, but useless. All four words were in the
    corpus.
    """

    def test_content_tokens_drops_stopwords_and_dedupes(self):
        from zolai.api.assistant_router import _content_tokens

        assert _content_tokens("nang bang hih thei") == ["nang", "bang", "hih", "thei"]
        assert _content_tokens("what is pasian") == ["pasian"]
        assert _content_tokens("pasian pasian phat") == ["pasian", "phat"]
        assert _content_tokens("what does it mean") == []

    def test_content_tokens_is_bounded(self):
        from zolai.api.assistant_router import MAX_DICTIONARY_LOOKUPS, _content_tokens

        # Digits are stripped before the length check, so "w0" -> "w" is rejected
        # as too short and "token0" -> "token" dedupes to one. The fixture needs
        # distinct alphabetic words.
        words = [
            "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
            "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi",
            "rho", "sigma", "tau", "upsilon",
        ]
        assert len(_content_tokens(" ".join(words))) == MAX_DICTIONARY_LOOKUPS

    def test_first_token_still_works(self):
        from zolai.api.assistant_router import _first_token

        assert _first_token("what is pasian") == "pasian"
        assert _first_token("!!!") == ""


class TestUngroundedAnswerIsGrounded:
    """A model that answers without calling tools must not ship ungrounded.

    ``run_agent_loop`` returns the instant a turn contains no ``<<<TOOL>>>`
    block, so a memory-only reply reached the user with zero citations. For
    "nang bang hih thei" that produced a confident grammar verdict and an
    admission of "no dictionary evidence" -- while all four words were in the
    dictionary the whole time.
    """

    def test_no_tool_call_triggers_a_grounding_pass(self, monkeypatch):
        from zolai.api import assistant_router as ar

        seen: list[int] = []

        def memory_only_chat(messages, tools=None):
            # First turn: answers from memory, calls nothing.
            if not any("TOOL_RESULT" in str(m.get("content", "")) for m in messages):
                return {"text": "I cannot look that up right now."}
            # Second turn (grounding): acknowledges the injected evidence.
            seen.append(1)
            return {"text": "nang = weak, bang = what [dictionary]."}

        monkeypatch.setattr(ar, "llm_allowed", lambda: True)
        monkeypatch.setattr(ar, "make_chat_fn", lambda **kw: (memory_only_chat, {"catalog_id": "stub", "model": "stub"}))

        result = ar.run_assistant("nang bang hih thei", persist=False)

        assert seen, "grounding turn never happened -- ungrounded reply shipped"
        assert result["retrieval_only"] is False, "must still generate, not degrade"
        assert result["tool_calls"], "no evidence recorded despite grounding"
        assert result["citations"], "grounded reply still has no citations"

    def test_honest_no_evidence_is_not_treated_as_a_defect(self, monkeypatch):
        from zolai.api import assistant_router as ar

        def empty_kb_chat(messages, tools=None):
            return {"text": "I have no evidence for that."}

        monkeypatch.setattr(ar, "llm_allowed", lambda: True)
        monkeypatch.setattr(ar, "make_chat_fn", lambda **kw: (empty_kb_chat, {"catalog_id": "stub", "model": "stub"}))

        # A greeting the KB has nothing for: keep the model's reply as-is.
        result = ar.run_assistant("hello there", persist=False)

        assert result["retrieval_only"] is False
        assert "no evidence" in result["answer"].lower() or result["answer"]


class TestPromptDoesNotInviteFalseModeClaims:
    """The prompt used to say "if no active LLM provider, you are in
    retrieval-only mode — say so."

    A model cannot know whether a provider is active, and it guessed wrong:
    replies generated by pcore-brain asserted "no active LLM provider is
    configured, so I'm in retrieval-only mode" and "only retrieval tools are
    available here". The server already records the mode truthfully in
    `retrieval_only`, so the model should never narrate it.
    """

    @staticmethod
    def _prompt() -> str:
        from zolai.api.assistant_router import PUBLIC_ASSISTANT_PROMPT

        return PUBLIC_ASSISTANT_PROMPT

    def test_prompt_tells_the_model_not_to_claim_a_mode(self):
        assert "NEVER claim to be in 'retrieval-only mode'" in self._prompt()
        # The server owns this flag; the model must not narrate it.
        assert "the server records the mode itself" in self._prompt()

    def test_prompt_no_longer_asks_the_model_to_say_so(self):
        # This was the instruction that produced false claims such as
        # "no active LLM provider is configured" in replies pcore-brain
        # had just generated.
        assert "you are in retrieval-only mode — say so" not in self._prompt()
        assert "If no active LLM provider, you are in retrieval-only" not in self._prompt()

    def test_mode_is_reported_by_the_server_not_the_model(self, monkeypatch):
        from zolai.api import assistant_router as ar

        def chat(messages, tools=None):
            return {"text": "nang = weak [dictionary:nang]"}

        monkeypatch.setattr(ar, "llm_allowed", lambda: True)
        monkeypatch.setattr(ar, "make_chat_fn", lambda **kw: (chat, {"catalog_id": "stub", "model": "stub"}))

        out = ar.run_assistant("nang", persist=False)

        assert out["retrieval_only"] is False, "server must own the mode flag"
        assert out["provider"] == "stub"
