"""CIMD client resolution, RFC 9207 ``iss``, RFC 7591 echo fields, and the
SSE-negotiated MCP reply.

Pattern follows ``tests/test_oauth_mcp.py``: lifespan-free local apps (never
``create_app``), a temp SQLite through the monkeypatched ``_get_manager``
seam — never the live ``data/zolai.db``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zolai.api import auth as api_auth
from zolai.api import mcp_router, oauth_router, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.auth import oauth
from zolai.data.database import DatabaseManager

# ChatGPT's real production CIMD document shape (developers.openai.com/
# plugins/build/auth): stable client_id = document URL, plural auth-methods
# field with the legacy singular preference alongside.
CHATGPT_CLIENT_ID = "https://chatgpt.com/oauth/client.json"
CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
CHATGPT_DOC = {
    "client_id": CHATGPT_CLIENT_ID,
    "client_name": "ChatGPT",
    "redirect_uris": [CHATGPT_REDIRECT],
    "grant_types": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    "token_endpoint_auth_method": "none",
}

_VERIFIER = "a" * 43  # unreserved charset, minimum length — good enough for S256
_CHALLENGE = oauth.s256_challenge(_VERIFIER)


@pytest.fixture()
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(oauth_router.router)
    application.include_router(mcp_router.router)
    application.include_router(mcp_router.root_router)
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    # starlette ≥1.x TestClient follows redirects by default, which would
    # replay the 302 to the client's (external) redirect_uri and report the
    # follow-up 404 instead of the authorization response under test.
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def oauth_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'oauth.db'}")
    mgr.init_db()
    from zolai.data.migrations import create_api_keys_table

    create_api_keys_table(mgr)
    monkeypatch.setattr(oauth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(api_auth, "_get_manager", lambda: mgr)
    oauth.ensure_oauth_tables()
    monkeypatch.setattr(session_auth, "_get_manager", lambda: mgr)
    yield mgr
    mgr.dispose()


@pytest.fixture(autouse=True)
def _clean_cimd_state() -> Iterator[None]:
    oauth._cimd_cache.clear()
    reset_rate_limiter()
    yield
    oauth._cimd_cache.clear()
    reset_rate_limiter()


@pytest.fixture()
def cimd_doc(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the network fetch with a recorder; returns the call log."""
    calls: list[str] = []

    def fake_fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return json.loads(json.dumps(CHATGPT_DOC))

    monkeypatch.setattr(oauth, "_cimd_fetch", fake_fetch)
    return calls


# ---------------------------------------------------------------------------
# Discovery: the two new advertisement flags
# ---------------------------------------------------------------------------


class TestNewMetadataFlags:
    @pytest.mark.parametrize(
        "path",
        (
            "/.well-known/oauth-authorization-server",
            "/.well-known/openid-configuration",
            "/mcp/.well-known/oauth-authorization-server",
        ),
    )
    def test_cimd_and_iss_are_advertised(self, client: TestClient, path: str) -> None:
        doc = client.get(path).json()
        # ChatGPT only skips DCR (the Cloudflare-blocked path) when it sees this:
        assert doc["client_id_metadata_document_supported"] is True
        assert doc["authorization_response_iss_parameter_supported"] is True


# ---------------------------------------------------------------------------
# CIMD — client_id URL syntax
# ---------------------------------------------------------------------------


class TestCimdSyntax:
    @pytest.mark.parametrize(
        "client_id",
        [
            CHATGPT_CLIENT_ID,
            "https://claude.ai/mcp/client.json",
            "https://example.com/.well-known/oauth/client-metadata.json",
        ],
    )
    def test_accepts_document_urls(self, client_id: str) -> None:
        assert oauth.is_client_id_metadata_document(client_id)

    @pytest.mark.parametrize(
        "client_id",
        [
            "",
            "zolai_cid_whatever",
            "http://example.com/client.json",  # https only
            "https://example.com",  # no path
            "https://example.com/",  # path is only "/"
            "https://example.com/client.json?x=1",  # query
            "https://example.com/client.json#frag",  # fragment
            "https://user:pw@example.com/client.json",  # userinfo
            "https://example.com:8443/client.json",  # non-default port
            "https://127.0.0.1/client.json",  # loopback literal
            "https://10.0.0.8/client.json",  # private literal
            "https://169.254.169.254/latest/meta-data.json",  # cloud IMDS
            "https://localhost/client.json",
            "https://internal.local/client.json",
            "https://" + "a" * 3000 + "/client.json",  # absurd length
        ],
    )
    def test_rejects_non_documents(self, client_id: str) -> None:
        assert oauth.is_client_id_metadata_document(client_id) is False


# ---------------------------------------------------------------------------
# CIMD — document policy
# ---------------------------------------------------------------------------


class TestDocumentPolicy:
    def test_valid_document_becomes_public_client_record(self) -> None:
        record = oauth._client_from_metadata_document(
            CHATGPT_CLIENT_ID, dict(CHATGPT_DOC)
        )
        assert record["client_id"] == CHATGPT_CLIENT_ID
        assert record["token_endpoint_auth_method"] == "none"
        assert record["client_secret_hash"] is None
        assert record["redirect_uris"] == [CHATGPT_REDIRECT]

    @pytest.mark.parametrize(
        "mangle, reason",
        [
            ({"client_id": "https://evil.example/x.json"}, "identity mismatch"),
            ({"token_endpoint_auth_methods_supported": ["private_key_jwt"]}, "no public option"),
            (
            {
                "token_endpoint_auth_methods_supported": None,
                "token_endpoint_auth_method": "client_secret_basic",
            },
            "legacy secret-only",
        ),
            ({"redirect_uris": ["http://insecure.example/cb"]}, "cleartext redirect"),
            ({"redirect_uris": []}, "empty redirects"),
            ({"grant_types": ["implicit"]}, "unsupported grant"),
            ({}, "missing redirect_uris"),
        ],
    )
    def test_policy_rejects(self, mangle: dict, reason: str) -> None:
        doc = {k: v for k, v in CHATGPT_DOC.items() if k != "client_id"}
        doc["client_id"] = CHATGPT_CLIENT_ID
        doc.update(mangle)
        if not mangle:
            doc.pop("redirect_uris", None)
        with pytest.raises(oauth.ClientMetadataError):
            oauth._client_from_metadata_document(CHATGPT_CLIENT_ID, doc)


class TestResolveAndCache:
    def test_record_cached_after_first_fetch(self, cimd_doc: list[str]) -> None:
        first = oauth.resolve_client_metadata_document(CHATGPT_CLIENT_ID)
        second = oauth.resolve_client_metadata_document(CHATGPT_CLIENT_ID)
        assert first is not None and second is not None
        assert first["client_name"] == "ChatGPT"
        assert len(cimd_doc) == 1, "second lookup must hit the cache"

    def test_negative_result_is_cached_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def bad_fetch(url: str) -> dict[str, Any]:
            calls.append(url)
            return {**CHATGPT_DOC, "client_id": "https://elsewhere.example/x.json"}

        monkeypatch.setattr(oauth, "_cimd_fetch", bad_fetch)
        assert oauth.resolve_client_metadata_document(CHATGPT_CLIENT_ID) is None
        assert oauth.resolve_client_metadata_document(CHATGPT_CLIENT_ID) is None
        assert len(calls) == 1, "a rejected document must not re-fetch per request"

    def test_non_document_ids_never_reach_the_fetcher(
        self, cimd_doc: list[str]
    ) -> None:
        assert oauth.resolve_client_metadata_document("zolai_cid_x") is None
        assert cimd_doc == []

    def test_get_client_falls_back_to_cimd(self, oauth_db, cimd_doc) -> None:
        assert oauth.get_client("zolai_cid_not_registered") is None
        record = oauth.get_client(CHATGPT_CLIENT_ID)
        assert record is not None
        assert record["redirect_uris"] == [CHATGPT_REDIRECT]
        assert cimd_doc == [CHATGPT_CLIENT_ID]


# ---------------------------------------------------------------------------
# The authorize endpoint driving a CIMD client
# ---------------------------------------------------------------------------


def _authorize_params(client_id: str, redirect_uri: str, **extra: str) -> dict[str, str]:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "s-1",
        "code_challenge": _CHALLENGE,
        "code_challenge_method": "S256",
    }
    params.update(extra)
    return params


class TestAuthorizeWithCimd:
    def test_consent_page_renders_for_document_client(
        self, client: TestClient, oauth_db, cimd_doc: list[str]
    ) -> None:
        response = client.get(f"/oauth/authorize?{urlencode(_authorize_params(CHATGPT_CLIENT_ID, CHATGPT_REDIRECT))}")
        assert response.status_code == 200
        assert "ChatGPT" in response.text

    def test_unlisted_redirect_still_rejected(
        self, client: TestClient, oauth_db, cimd_doc: list[str]
    ) -> None:
        response = client.get(
            f"/oauth/authorize?{urlencode(_authorize_params(CHATGPT_CLIENT_ID, 'https://evil.example/cb'))}"
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"


class TestFullFlowCarriesIss:
    def test_code_redirect_iss_then_token_exchange(
        self, client: TestClient, oauth_db, cimd_doc: list[str]
    ) -> None:
        session_auth.create_user(
            username="probe", password="correct horse battery staple",
            role="member", actor="test",
        )
        response = client.post(
            "/oauth/authorize",
            data={
                **_authorize_params(CHATGPT_CLIENT_ID, CHATGPT_REDIRECT),
                "username": "probe",
                "password": "correct horse battery staple",
                "consent": "allow",
            },
        )
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        query = parse_qs(urlsplit(location).query)
        assert query["state"] == ["s-1"]
        # RFC 9207: exactly the advertised issuer (TestClient origin).
        assert query["iss"] == ["http://testserver"]

        token = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": CHATGPT_REDIRECT,
                "client_id": CHATGPT_CLIENT_ID,
                "code_verifier": _VERIFIER,
                "resource": "http://testserver/mcp",
            },
        )
        assert token.status_code == 200, token.text
        body = token.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"].startswith(oauth.ACCESS_TOKEN_PREFIX)
        assert body["refresh_token"].startswith(oauth.REFRESH_TOKEN_PREFIX)


# ---------------------------------------------------------------------------
# DCR — RFC 7591 echo fields
# ---------------------------------------------------------------------------


class TestDcrEcho:
    def test_scope_and_software_fields_are_echoed(self, client: TestClient, oauth_db) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "Cursor",
                "redirect_uris": ["http://127.0.0.1:8787/callback"],
                "scope": "catalog:read rag:read",
                "software_id": "cursor-mcp",
                "software_version": "2.0",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["scope"] == "catalog:read rag:read"
        assert body["software_id"] == "cursor-mcp"
        assert body["software_version"] == "2.0"

    def test_unknown_scope_still_rejected(self, client: TestClient, oauth_db) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "X",
                "redirect_uris": ["https://client.example/cb"],
                "scope": "not:a:scope",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"


# ---------------------------------------------------------------------------
# MCP transport — SSE-negotiated single-event reply
# ---------------------------------------------------------------------------

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    },
}


class TestSseNegotiation:
    def test_accept_event_stream_gets_one_sse_message_event(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/mcp", json=_INIT, headers={"Accept": "text/event-stream"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        lines = response.text.splitlines()
        assert lines[0] == "event: message"
        payload = json.loads(lines[1].removeprefix("data: "))
        assert payload["jsonrpc"] == "2.0" and payload["id"] == 1
        assert "result" in payload

    def test_json_accept_still_gets_plain_json(self, client: TestClient) -> None:
        response = client.post("/mcp", json=_INIT, headers={"Accept": "application/json"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["id"] == 1

    def test_notifications_never_carry_a_body(self, client: TestClient) -> None:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 202
        assert response.content == b""
