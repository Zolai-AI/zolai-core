"""P0 defect 2 — the public assistant chat never accepts a credential.

``POST /api/v1/assistant/chat`` sits in ``rbac.PUBLIC_ROUTES``: it is
reachable with **no credential at all**. MCP invariant M3 is that a public
tool/API must never accept a caller-supplied credential, yet ``ChatIn``
carried ``api_key`` / ``user_provider`` and the public route forwarded them
into the provider resolver — letting an anonymous caller steer spend onto a
key of their choosing (or probe key-shaped fields).

Pinned here:

- anonymous body with ``api_key`` / ``user_provider`` (or a smuggled
  ``token`` / ``authorization``) → **400** with the stable code
  ``caller_supplied_credentials_forbidden`` in the house envelope
  (``zolai/api/errors.py``), and the credential **value appears nowhere** in
  the raised detail;
- a bare ``{"message"}`` body still reaches ``run_assistant`` untouched;
- the admin route keeps accepting ``api_key`` / ``user_provider`` (it is
  gated on the resolved admin role, not on a client-declared field).

No FastAPI app and no database: the route handler is called directly (FastAPI
decorators return the bare function), ``providers_router.validate_selection``
and ``run_assistant`` are stubbed, and the raw-body scan is fed a hand-built
``Request``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException, Request

from zolai.api import assistant_router, providers_router

SECRET = "sk-live-CANARY-9f8e7d6c5b4a"


def _request(payload: dict) -> Request:
    """A minimal ASGI ``Request`` whose cached raw body is ``payload``."""
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/assistant/chat",
        "raw_path": b"/api/v1/assistant/chat",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "state": {},
    }
    request = Request(scope)
    request._body = body  # what FastAPI's own parsing leaves behind
    return request


@pytest.fixture()
def calls(monkeypatch) -> list[dict]:
    """Record every run_assistant invocation; neuter the provider validator."""
    seen: list[dict] = []

    def _fake_run(message: str, **kwargs) -> dict:
        seen.append({"message": message, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(assistant_router, "run_assistant", _fake_run)
    monkeypatch.setattr(providers_router, "validate_selection", lambda *a, **k: None)
    return seen


def _post(body: assistant_router.ChatIn, payload: dict) -> dict:
    return asyncio.run(assistant_router.public_chat(body, _request(payload)))


class TestPublicRouteRejectsCredentials:
    def test_api_key_is_400_with_stable_code_and_no_leak(self, calls) -> None:
        payload = {"message": "hi", "api_key": SECRET}
        with pytest.raises(HTTPException) as exc:
            _post(assistant_router.ChatIn(**payload), payload)
        assert exc.value.status_code == 400
        detail = exc.value.detail
        assert detail["error"] == "caller_supplied_credentials_forbidden"
        assert isinstance(detail["message"], str) and detail["message"]
        assert "api_key" in detail["fields"]
        # The credential value appears NOWHERE in the response envelope.
        assert SECRET not in json.dumps(detail)
        # Nothing ran: no provider validation, no assistant call.
        assert calls == []

    def test_user_provider_is_400(self, calls) -> None:
        payload = {"message": "hi", "user_provider": "openrouter"}
        with pytest.raises(HTTPException) as exc:
            _post(assistant_router.ChatIn(**payload), payload)
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "caller_supplied_credentials_forbidden"
        assert calls == []

    def test_smuggled_token_and_authorization_keys_are_400(self, calls) -> None:
        # Neither is a ChatIn field (pydantic would ignore them) — the raw
        # body scan still refuses the request.
        for field in ("token", "authorization"):
            payload = {"message": "hi", field: SECRET}
            with pytest.raises(HTTPException) as exc:
                _post(assistant_router.ChatIn(message="hi"), payload)
            assert exc.value.status_code == 400, field
            assert exc.value.detail["error"] == "caller_supplied_credentials_forbidden"
            assert field in exc.value.detail["fields"]
            assert SECRET not in json.dumps(exc.value.detail)
        assert calls == []

    def test_empty_string_credential_is_treated_as_absent(self, calls) -> None:
        # ``api_key: ""`` carries no credential; the request proceeds.
        payload = {"message": "hi", "api_key": ""}
        out = _post(assistant_router.ChatIn(message="hi", api_key=""), payload)
        assert out == {"ok": True}
        assert len(calls) == 1
        # And the public path never forwards a key slot at all.
        assert "api_key" not in calls[0]
        assert "user_provider" not in calls[0]


class TestBareMessageStillWorks:
    def test_bare_message_reaches_run_assistant(self, calls) -> None:
        payload = {"message": "pasian"}
        out = _post(assistant_router.ChatIn(message="pasian"), payload)
        assert out == {"ok": True}
        # The hand-built request has empty ``state`` — no resolved credential —
        # so issue #14's gate pins it to the rule path (allow_llm=False).
        assert calls == [
            {"message": "pasian", "admin": False, "persist": False,
             "provider": None, "model": None, "allow_llm": False}
        ]


class TestAdminRouteUnbroken:
    def test_admin_chat_still_forwards_api_key_and_user_provider(
        self, monkeypatch, calls
    ) -> None:
        """The admin route keeps the feature — gated on the resolved role."""
        body = assistant_router.ChatIn(
            message="hi", api_key=SECRET, user_provider="openrouter", persist=True
        )
        # admin_chat is a sync handler (FastAPI runs it in the threadpool).
        out = assistant_router.admin_chat(body, _request({"message": "hi"}))
        assert out == {"ok": True}
        assert calls[0]["api_key"] == SECRET
        assert calls[0]["user_provider"] == "openrouter"
        assert calls[0]["admin"] is True

    def test_admin_route_is_declared_strict_admin(self) -> None:
        """The gate is the resolved admin role, never a body field."""
        from zolai.api import rbac

        assert rbac.classify_route("POST", "/api/v1/admin/assistant/chat") == "admin"
        assert ("POST", "/api/v1/assistant/chat") in rbac.PUBLIC_ROUTES
