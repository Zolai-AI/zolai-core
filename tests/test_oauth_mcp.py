"""W6 — OAuth 2.1 for the MCP endpoint (discovery → register → authorize → token → challenge).

Discovery (this file, first tests): every RFC 8414/9728 path ChatGPT and the
Gemini app probe is served with the required fields and **absolute** endpoint
URLs; the documents are published unconditionally (independently of the
strict flag); ``scopes_supported`` is a strict subset of the frozen 33-action
vocabulary; ``plain`` is never advertised.

Deliberately **does not instantiate ``zolai.api.server.create_app``** — its
lifespan runs ``run_all_migrations()`` against the live 2.4 GB
``data/zolai.db``. Local ``FastAPI()`` apps (router + middleware only, no
lifespan) and monkeypatched ``_get_manager`` seams are the pattern here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from zolai.api import auth, oauth_router, session_auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.oauth_router import DEFAULT_REGISTER_RPM
from zolai.auth import oauth
from zolai.data.database import DatabaseManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    """A lifespan-free app carrying only the OAuth routes (no live DB)."""
    application = FastAPI()
    application.include_router(oauth_router.router)
    return application


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def strict_flag(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("ZOLAI_MCP_OAUTH_REQUIRED", "1")
    yield "1"
    monkeypatch.delenv("ZOLAI_MCP_OAUTH_REQUIRED", raising=False)


AS_PATHS = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
    "/mcp/.well-known/oauth-authorization-server",
)
PRM_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/mcp/.well-known/oauth-protected-resource",
)


# ---------------------------------------------------------------------------
# RFC 8414 — authorization-server metadata
# ---------------------------------------------------------------------------


class TestAuthorizationServerMetadata:
    @pytest.mark.parametrize("path", AS_PATHS)
    def test_served_at_every_probed_path(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200, f"{path} must not 404 (ChatGPT's exact failure)"
        assert response.headers["content-type"].startswith("application/json")
        # Never a challenge — discovery needs no credential, in any flag mode.
        assert "www-authenticate" not in response.headers

    @pytest.mark.parametrize("path", AS_PATHS)
    def test_required_rfc_fields_and_absolute_urls(
        self, client: TestClient, path: str
    ) -> None:
        doc = client.get(path).json()
        assert doc["issuer"] == "http://testserver"
        for key in (
            "authorization_endpoint",
            "token_endpoint",
            "registration_endpoint",
        ):
            url = doc[key]
            # Absolute, and pointing back at the host that served the document.
            assert url.startswith("http://testserver/"), f"{key}={url}"
        assert doc["response_types_supported"] == ["code"]
        assert doc["grant_types_supported"] == ["authorization_code", "refresh_token"]
        assert doc["response_modes_supported"] == ["query"]
        assert "client_secret_post" in doc["token_endpoint_auth_methods_supported"]
        assert "none" in doc["token_endpoint_auth_methods_supported"]

    @pytest.mark.parametrize("path", AS_PATHS)
    def test_pkce_s256_only_plain_never_advertised(
        self, client: TestClient, path: str
    ) -> None:
        methods = client.get(path).json()["code_challenge_methods_supported"]
        assert methods == ["S256"], "OAuth 2.1: PKCE mandatory, S256 only, plain forbidden"

    def test_scopes_supported_is_subset_of_frozen_vocabulary(
        self, client: TestClient
    ) -> None:
        scopes = client.get(AS_PATHS[0]).json()["scopes_supported"]
        assert scopes, "a grantable scope vocabulary must be advertised"
        unknown = [s for s in scopes if s not in auth.VALID_ACTIONS]
        assert unknown == [], f"scope outside the frozen 33-action vocabulary: {unknown}"

    def test_discovery_survives_the_strict_flag(
        self, client: TestClient, strict_flag: str
    ) -> None:
        """Documents are published unconditionally — strict mode changes nothing."""
        for path in AS_PATHS + PRM_PATHS:
            assert client.get(path).status_code == 200, path

    def test_forwarded_origin_is_honoured(self, client: TestClient) -> None:
        response = client.get(
            AS_PATHS[0], headers={"X-Forwarded-Proto": "https", "Host": "mcp.zolai.space"}
        )
        doc = response.json()
        assert doc["issuer"] == "https://mcp.zolai.space"
        assert doc["token_endpoint"] == "https://mcp.zolai.space/oauth/token"

    def test_public_origin_override_wins(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ZOLAI_PUBLIC_ORIGIN", "https://mcp.zolai.space/")
        doc = client.get(AS_PATHS[0]).json()
        assert doc["issuer"] == "https://mcp.zolai.space"


# ---------------------------------------------------------------------------
# RFC 9728 — protected-resource metadata
# ---------------------------------------------------------------------------


class TestProtectedResourceMetadata:
    @pytest.mark.parametrize("path", PRM_PATHS)
    def test_served_at_every_probed_path(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "www-authenticate" not in response.headers

    @pytest.mark.parametrize("path", PRM_PATHS)
    def test_resource_is_the_mcp_endpoint_and_points_back(
        self, client: TestClient, path: str
    ) -> None:
        doc = client.get(path).json()
        # ``resource`` is the URL a ChatGPT/Gemini operator enters by hand.
        assert doc["resource"] == "http://testserver/mcp"
        assert doc["authorization_servers"] == ["http://testserver"]
        assert doc["bearer_methods_supported"] == ["header"]
        scopes = doc["scopes_supported"]
        assert scopes and all(s in auth.VALID_ACTIONS for s in scopes)

    def test_www_authenticate_challenge_targets_this_document(self) -> None:
        """The header the strict flag emits must be a well-formed absolute URL."""
        scope = {
            "type": "http",
            "scheme": "https",
            "headers": [(b"host", b"mcp.zolai.space")],
        }
        value = oauth.www_authenticate(scope)
        assert value == (
            'Bearer resource_metadata='
            '"https://mcp.zolai.space/.well-known/oauth-protected-resource"'
        )
        # An invalid-token challenge adds RFC 6750 error fields, still literal.
        invalid = oauth.www_authenticate(scope, error="invalid_token")
        assert invalid.startswith(
            'Bearer resource_metadata='
            '"https://mcp.zolai.space/.well-known/oauth-protected-resource"'
        )
        assert 'error="invalid_token"' in invalid


# ---------------------------------------------------------------------------
# RFC 7591 — dynamic client registration
# ---------------------------------------------------------------------------


@pytest.fixture()
def oauth_db(tmp_path, monkeypatch) -> Iterator["DatabaseManager"]:
    """A temp SQLite store — **never** the live 2.4 GB ``data/zolai.db``."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path / 'oauth.db'}")
    mgr.init_db()
    from zolai.api import auth as api_auth
    from zolai.data.migrations import create_api_keys_table

    create_api_keys_table(mgr)
    monkeypatch.setattr(oauth, "_get_manager", lambda: mgr)
    monkeypatch.setattr(api_auth, "_get_manager", lambda: mgr)
    oauth.ensure_oauth_tables()
    yield mgr
    mgr.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    reset_rate_limiter()
    yield
    reset_rate_limiter()


class TestClientRegistration:
    def test_confidential_client_secret_hashed_never_stored_plain(
        self, client: TestClient, oauth_db: DatabaseManager, tmp_path: Any
    ) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "ChatGPT connector",
                "redirect_uris": ["https://chatgpt.com/api/mcp/callback"],
            },
        )
        assert response.status_code == 201, response.text
        doc = response.json()
        assert doc["client_id"].startswith(oauth.CLIENT_ID_PREFIX)
        secret = doc["client_secret"]
        assert secret.startswith(oauth.CLIENT_SECRET_PREFIX)
        assert doc["client_secret_expires_at"] == 0
        assert response.headers["cache-control"] == "no-store"
        assert doc["grant_types"] == ["authorization_code", "refresh_token"]
        assert doc["token_endpoint_auth_method"] == "client_secret_post"

        with oauth_db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT client_secret_hash FROM oauth_clients WHERE client_id = :c"),
                {"c": doc["client_id"]},
            ).first()
        assert row is not None
        assert row[0] == oauth.hash_token(secret)
        # The plaintext secret appears in **no** file on disk, anywhere.
        for path in tmp_path.iterdir():
            blob = path.read_bytes()
            assert secret.encode() not in blob, f"plaintext secret leaked into {path.name}"

    def test_public_client_registration_has_no_secret(
        self, client: TestClient, oauth_db: DatabaseManager
    ) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "Gemini CLI",
                "redirect_uris": ["http://localhost:1441/callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code == 201, response.text
        doc = response.json()
        assert "client_secret" not in doc
        assert doc["token_endpoint_auth_method"] == "none"
        stored = oauth.get_client(doc["client_id"])
        assert stored is not None and stored["client_secret_hash"] is None
        # Loopback http is the native-app allowance (RFC 8252 §7.3).
        assert stored["redirect_uris"] == ["http://localhost:1441/callback"]

    @pytest.mark.parametrize(
        "metadata, reason",
        [
            ({"client_name": "x", "redirect_uris": ["http://evil.example/cb"]},
             "plain http off-loopback"),
            ({"client_name": "x", "redirect_uris": ["https://ok.example/cb#frag"]},
             "fragment"),
            ({"client_name": "x", "redirect_uris": []}, "empty uris"),
            ({"redirect_uris": ["https://ok.example/cb"]}, "missing client_name"),
            ({"client_name": "x"}, "missing redirect_uris"),
            ({"client_name": "x", "redirect_uris": ["https://ok.example/cb"],
              "grant_types": ["password"]}, "unsupported grant"),
            ({"client_name": "x", "redirect_uris": ["https://ok.example/cb"],
              "response_types": ["token"]}, "unsupported response type"),
            ({"client_name": "x", "redirect_uris": ["https://ok.example/cb"],
              "token_endpoint_auth_method": "jwt-bearer"}, "unsupported auth method"),
            ({"client_name": "x",
              "redirect_uris": ["ftp://files.example/cb"]}, "non-http scheme"),
        ],
    )
    def test_invalid_metadata_is_invalid_client_metadata(
        self, client: TestClient, oauth_db: DatabaseManager, metadata: dict, reason: str
    ) -> None:
        response = client.post("/oauth/register", json=metadata)
        assert response.status_code == 400, f"{reason}: {response.text}"
        payload = response.json()
        assert payload["error"] == "invalid_client_metadata", reason
        assert "detail" not in payload  # RFC 6749 §5.2 top-level shape

    @pytest.mark.parametrize("scope", ["not-a-scope", "rag:read_everything"])
    def test_registration_scope_must_be_in_the_frozen_vocabulary(
        self, client: TestClient, oauth_db: DatabaseManager, scope: str
    ) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "x",
                "redirect_uris": ["https://ok.example/cb"],
                "scope": scope,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"

    def test_registration_accepts_a_valid_frozen_scope(
        self, client: TestClient, oauth_db: DatabaseManager
    ) -> None:
        response = client.post(
            "/oauth/register",
            json={
                "client_name": "x",
                "redirect_uris": ["https://ok.example/cb"],
                "scope": "rag:read dataset:read",
            },
        )
        assert response.status_code == 201, response.text

    def test_verify_client_secret_confidential_vs_public(
        self, client: TestClient, oauth_db: DatabaseManager
    ) -> None:
        confidential = client.post(
            "/oauth/register",
            json={
                "client_name": "c",
                "redirect_uris": ["https://ok.example/cb"],
            },
        ).json()
        stored = oauth.get_client(confidential["client_id"])
        assert stored is not None
        assert oauth.verify_client_secret(stored, confidential["client_secret"]) is True
        assert oauth.verify_client_secret(stored, confidential["client_secret"] + "x") is False
        assert oauth.verify_client_secret(stored, None) is False
        assert oauth.verify_client_secret(stored, "") is False

        public = client.post(
            "/oauth/register",
            json={
                "client_name": "p",
                "redirect_uris": ["http://localhost:1/cb"],
                "token_endpoint_auth_method": "none",
            },
        ).json()
        stored_public = oauth.get_client(public["client_id"])
        assert stored_public is not None
        assert oauth.verify_client_secret(stored_public, None) is True
        # A public client cannot be impersonated by *presenting* a secret either.
        assert oauth.verify_client_secret(stored_public, "anything") is False

    def test_registration_is_rate_limited_per_ip(
        self, client: TestClient, oauth_db: DatabaseManager
    ) -> None:
        payload = {
            "client_name": "flood",
            "redirect_uris": ["https://ok.example/cb"],
        }
        for _ in range(DEFAULT_REGISTER_RPM):
            assert client.post("/oauth/register", json=payload).status_code == 201
        blocked = client.post("/oauth/register", json=payload)
        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers

    def test_unknown_client_id_is_not_found(self, oauth_db: DatabaseManager) -> None:
        assert oauth.get_client("zolai_cid_nope") is None
        assert oauth.get_client(None) is None


# ---------------------------------------------------------------------------
# RFC 6749 §4.1 — authorize: PKCE (S256 only) + consent + code issuance
# ---------------------------------------------------------------------------

#: RFC 7636 Appendix B test vector (verifier -> challenge).
_PKCE_VECTOR_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
_PKCE_VECTOR_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _verifier() -> str:
    """A syntactically valid 43-char code_verifier."""
    return "A" * 43


def _authorize_query(client_id: str, redirect_uri: str, **overrides: str) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": _PKCE_VECTOR_CHALLENGE,
        "code_challenge_method": "S256",
        "state": "xyz-123",
    }
    params.update(overrides)
    return urlencode(params)


@pytest.fixture()
def user_db(oauth_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Temp store + one real account through the *existing* identity layer."""
    monkeypatch.setattr(session_auth, "_get_manager", lambda: oauth_db)
    return session_auth.create_user(
        username="founder",
        password="correct horse battery staple",
        role="admin",
        actor="test",
    )


@pytest.fixture()
def registered(client: TestClient, oauth_db: DatabaseManager) -> dict[str, Any]:
    response = client.post(
        "/oauth/register",
        json={
            "client_name": "Test Connector",
            "redirect_uris": ["https://client.example/cb"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestPkce:
    def test_rfc_7636_appendix_b_vector(self) -> None:
        assert oauth.s256_challenge(_PKCE_VECTOR_VERIFIER) == _PKCE_VECTOR_CHALLENGE

    def test_validate_accepts_s256_and_normalizes(self) -> None:
        challenge, method = oauth.validate_code_challenge(_PKCE_VECTOR_CHALLENGE, "S256")
        assert (challenge, method) == (_PKCE_VECTOR_CHALLENGE, "S256")

    @pytest.mark.parametrize(
        "challenge, method, needle",
        [
            (None, "S256", "missing"),
            ("", "S256", "missing"),
            (_PKCE_VECTOR_CHALLENGE, None, "missing"),
            (_PKCE_VECTOR_CHALLENGE, "", "missing"),
            (_PKCE_VECTOR_CHALLENGE, "plain", "plain"),
            (_PKCE_VECTOR_CHALLENGE, "PLAIN", "plain"),
            (_PKCE_VECTOR_CHALLENGE, "S512", "unsupported"),
            ("too-short", "S256", "43-128"),
            ("!" * 43, "S256", "unreserved"),
        ],
    )
    def test_validate_rejects(
        self, challenge: str | None, method: str | None, needle: str
    ) -> None:
        with pytest.raises(oauth.PkceError) as excinfo:
            oauth.validate_code_challenge(challenge, method)
        assert needle in str(excinfo.value).lower()

    def test_verify_correct_verifier_only(self) -> None:
        assert oauth.verify_code_verifier(
            _PKCE_VECTOR_VERIFIER, "S256", _PKCE_VECTOR_CHALLENGE
        )
        assert not oauth.verify_code_verifier("B" * 43, "S256", _PKCE_VECTOR_CHALLENGE)
        assert not oauth.verify_code_verifier(None, "S256", _PKCE_VECTOR_CHALLENGE)
        # plain can never succeed even against a stored row that says so.
        assert not oauth.verify_code_verifier(
            _PKCE_VECTOR_VERIFIER, "plain", _PKCE_VECTOR_CHALLENGE
        )
        # Length/charset guards fail *before* any comparison.
        assert not oauth.verify_code_verifier("short", "S256", _PKCE_VECTOR_CHALLENGE)
        assert not oauth.verify_code_verifier("!" * 43, "S256", _PKCE_VECTOR_CHALLENGE)


class TestAuthorizeEndpoint:
    def test_get_renders_login_and_consent_form(
        self, client: TestClient, oauth_db: DatabaseManager, registered: dict
    ) -> None:
        query = _authorize_query(
            registered["client_id"], registered["redirect_uris"][0]
        )
        response = client.get(f"/oauth/authorize?{query}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        body = response.text
        assert "Test Connector" in body
        assert 'name="username"' in body and 'name="password"' in body
        assert 'name="code_challenge" value=""' not in body
        assert _PKCE_VECTOR_CHALLENGE in body  # bound into the form (hidden)
        assert 'name="state" value="xyz-123"' in body
        assert 'action="/oauth/authorize"' in body

    @pytest.mark.parametrize(
        "overrides, error",
        [
            ({"code_challenge_method": "plain"}, "invalid_request"),
            ({"code_challenge": ""}, "invalid_request"),
            ({"code_challenge_method": ""}, "invalid_request"),
            ({"client_id": "zolai_cid_missing"}, "invalid_client"),
            ({"redirect_uri": "https://attacker.example/cb"}, "invalid_request"),
            ({"response_type": "token"}, "unsupported_response_type"),
            ({"scope": "not-a-real-scope"}, "invalid_scope"),
        ],
    )
    def test_get_refuses_before_showing_a_login_form(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        registered: dict,
        overrides: dict,
        error: str,
    ) -> None:
        query = _authorize_query(
            **{
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                **overrides,
            }
        )
        response = client.get(f"/oauth/authorize?{query}")
        # Refused directly (400), never by redirecting to a URI, and never by
        # showing a form whose submission could not succeed.
        assert response.status_code == 400, response.text
        assert response.json()["error"] == error
        assert "username" not in response.text

    @pytest.mark.parametrize("method_key", ["code_challenge", "code_challenge_method"])
    def test_plain_and_missing_are_always_refused(
        self, client: TestClient, oauth_db: DatabaseManager, registered: dict, method_key: str
    ) -> None:
        """PKCE is mandatory: dropping either half never yields a form."""
        query = _authorize_query(
            registered["client_id"],
            registered["redirect_uris"][0],
            **({"code_challenge_method": "plain"} if method_key == "code_challenge_method" else {}),
        )
        if method_key == "code_challenge":
            query = query.replace(_PKCE_VECTOR_CHALLENGE, "")  # challenge absent
        response = client.get(f"/oauth/authorize?{query}")
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"

    def test_post_verifies_credentials_and_redirects_with_code(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        tmp_path: Any,
    ) -> None:
        from urllib.parse import parse_qs, urlsplit

        query = _authorize_query(
            registered["client_id"], registered["redirect_uris"][0]
        )
        form = parse_qs(query)
        flat = {k: v[0] for k, v in form.items()}
        flat["username"] = "founder"
        flat["password"] = "correct horse battery staple"
        response = client.post(
            "/oauth/authorize",
            data=flat,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        assert location.startswith(registered["redirect_uris"][0])
        redirected = parse_qs(urlsplit(location).query)
        code = redirected["code"][0]
        assert code.startswith(oauth.AUTH_CODE_PREFIX)
        assert redirected["state"] == ["xyz-123"]
        assert response.headers["cache-control"] == "no-store"

        # Hashed at rest; plaintext code nowhere on disk; admin role grants
        # exactly the existing admin scope set (no new vocabulary).
        with oauth_db.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT code_hash, consumed_at, scopes, user_id "
                    "FROM oauth_authorization_codes WHERE code_hash = :h"
                ),
                {"h": oauth.hash_token(code)},
            ).first()
        assert row is not None
        assert row[0] == oauth.hash_token(code)
        assert row[1] is None
        assert sorted(__import__("json").loads(row[2])) == sorted(
            session_auth.ADMIN_SCOPES
        )
        assert row[3] == user_db["id"]
        for path in tmp_path.iterdir():
            assert code.encode() not in path.read_bytes(), path.name

    def test_post_narrows_a_requested_grant_to_the_role(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A requested scope can only narrow — never widen."""
        from urllib.parse import parse_qs

        session_auth.set_user_role("founder", role="member", actor="test")
        query = _authorize_query(
            registered["client_id"],
            registered["redirect_uris"][0],
            scope="dataset:read settings:write",  # settings:write is admin-only
        )
        flat = {k: v[0] for k, v in parse_qs(query).items()}
        flat["username"] = "founder"
        flat["password"] = "correct horse battery staple"
        response = client.post(
            "/oauth/authorize",
            data=flat,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        from urllib.parse import parse_qs as _pq
        from urllib.parse import urlsplit

        code = _pq(urlsplit(response.headers["location"]).query)["code"][0]
        with oauth_db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT scopes FROM oauth_authorization_codes WHERE code_hash = :h"),
                {"h": oauth.hash_token(code)},
            ).first()
        import json as _json

        # The member keeps dataset:read; settings:write (admin) is dropped.
        assert _json.loads(row[0]) == ["dataset:read"]

    def test_post_bad_password_gets_the_form_back_401(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        query = _authorize_query(
            registered["client_id"], registered["redirect_uris"][0]
        )
        from urllib.parse import parse_qs

        flat = {k: v[0] for k, v in parse_qs(query).items()}
        flat["username"] = "founder"
        flat["password"] = "wrong password entirely"
        response = client.post(
            "/oauth/authorize",
            data=flat,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("text/html")
        assert "Invalid username or password." in response.text
        # One shape for unknown user and wrong password (no enumeration), and
        # no code was minted.
        assert "zolai_ac_" not in response.text
        with oauth_db.engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM oauth_authorization_codes")
            ).scalar()
        assert count == 0

        flat["password"] = "correct horse battery staple"
        flat["username"] = "no-such-user"
        second = client.post(
            "/oauth/authorize",
            data=flat,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert second.status_code == 401
        assert "Invalid username or password." in second.text

    def test_post_requires_urlencoded_form(
        self, client: TestClient, oauth_db: DatabaseManager, registered: dict
    ) -> None:
        response = client.post("/oauth/authorize", json={"client_id": "x"})
        assert response.status_code == 415
        assert response.json()["error"] == "unsupported_media_type"


# ---------------------------------------------------------------------------
# RFC 6749 §3.2 / §5 — token endpoint + access-token resolution
# ---------------------------------------------------------------------------


def _authorize_and_get_code(
    client: TestClient, registered: dict, *, username: str = "founder"
) -> tuple[str, str]:
    """Full browser step: login + consent → the 302 carries the code.

    Returns ``(code, code_verifier)`` — the verifier is the fixed PKCE
    vector every test in this module uses.
    """
    from urllib.parse import parse_qs, urlsplit

    query = _authorize_query(
        registered["client_id"], registered["redirect_uris"][0]
    )
    flat = {k: v[0] for k, v in parse_qs(query).items()}
    flat["username"] = username
    flat["password"] = "correct horse battery staple"
    response = client.post(
        "/oauth/authorize",
        data=flat,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    return code, _PKCE_VECTOR_VERIFIER


def _exchange_code(
    client: TestClient, registered: dict, code: str, verifier: str
) -> Any:
    form: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": registered["client_id"],
        "redirect_uri": registered["redirect_uris"][0],
        "code": code,
        "code_verifier": verifier,
    }
    if registered.get("client_secret"):
        form["client_secret"] = registered["client_secret"]
    return client.post(
        "/oauth/token",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


class TestTokenEndpoint:
    def test_full_round_trip_grants_a_narrowed_bearer_principal(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        """register → authorize → token → Bearer resolves, tier = role's."""
        from zolai.api import rbac

        code, verifier = _authorize_and_get_code(client, registered)
        response = _exchange_code(client, registered, code, verifier)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == oauth.access_ttl_seconds()
        assert body["access_token"].startswith(oauth.ACCESS_TOKEN_PREFIX)
        assert body["refresh_token"].startswith(oauth.REFRESH_TOKEN_PREFIX)
        assert sorted(body["scope"].split()) == sorted(session_auth.ADMIN_SCOPES)

        # The Bearer token resolves to a principal whose tier is the *user's*
        # role via the existing rbac — admin here, because founder is admin.
        record = oauth.resolve_access_token(body["access_token"])
        assert record is not None
        assert record["auth_source"] == "oauth"
        assert record["username"] == "founder"
        assert record["user_id"] == user_db["id"]
        assert record["client_id"] == registered["client_id"]
        assert sorted(record["scopes"]) == sorted(session_auth.ADMIN_SCOPES)
        assert rbac.role_for(record) == "admin"

    def test_grant_only_narrows_never_widens(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A member's token resolves to member scope — no admin vocabulary."""
        from zolai.api import rbac

        session_auth.set_user_role("founder", role="member", actor="test")
        code, verifier = _authorize_and_get_code(client, registered)
        response = _exchange_code(client, registered, code, verifier)
        assert response.status_code == 200, response.text
        scopes = response.json()["scope"].split()
        assert scopes
        # Member scopes ⊂ admin scopes (admin is the superset); the grant is
        # exactly the member vocabulary — no admin-exclusive scope leaked.
        admin_exclusive = set(session_auth.ADMIN_SCOPES) - set(
            session_auth.MEMBER_SCOPES
        )
        assert not (set(scopes) & admin_exclusive)
        assert set(scopes) <= set(session_auth.MEMBER_SCOPES)
        record = oauth.resolve_access_token(response.json()["access_token"])
        assert record is not None
        assert rbac.role_for(record) == "member"

    def test_plaintext_tokens_never_reach_the_database(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        tmp_path: Any,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        body = _exchange_code(client, registered, code, verifier).json()
        with oauth_db.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT access_token_hash, refresh_token_hash FROM oauth_tokens")
            ).all()
        assert len(rows) == 1
        assert rows[0][0] == oauth.hash_token(body["access_token"])
        assert rows[0][1] == oauth.hash_token(body["refresh_token"])
        for path in tmp_path.iterdir():
            blob = path.read_bytes()
            assert body["access_token"].encode() not in blob, path.name
            assert body["refresh_token"].encode() not in blob, path.name

    def test_code_is_single_use_at_the_token_endpoint(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        first = _exchange_code(client, registered, code, verifier)
        assert first.status_code == 200, first.text
        second = _exchange_code(client, registered, code, verifier)
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"
        assert second.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "field, value, needle",
        [
            ("code", "zolai_ac_bogus", "invalid_grant"),
            ("code_verifier", "B" * 43, "invalid_grant"),  # wrong PKCE verifier
            ("redirect_uri", "https://other.example/cb", "invalid_grant"),
            ("code", "", "invalid_grant"),
        ],
    )
    def test_every_code_failure_is_one_invalid_grant(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        field: str,
        value: str,
        needle: str,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        form = {
            "grant_type": "authorization_code",
            "client_id": registered["client_id"],
            "client_secret": registered["client_secret"],
            "redirect_uri": registered["redirect_uris"][0],
            "code": code,
            "code_verifier": verifier,
            field: value,
        }
        response = client.post(
            "/oauth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["error"] == needle
        # The wire never reveals which check failed.
        assert set(payload) == {"error", "error_description"}

    def test_refresh_rotates_and_replays_are_refused(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        first = _exchange_code(client, registered, code, verifier).json()

        def _refresh(token: str) -> Any:
            return client.post(
                "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": registered["client_id"],
                    "client_secret": registered["client_secret"],
                    "refresh_token": token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        rotated = _refresh(first["refresh_token"])
        assert rotated.status_code == 200, rotated.text
        new = rotated.json()
        assert new["access_token"] != first["access_token"]
        assert new["refresh_token"] != first["refresh_token"]
        assert new["token_type"] == "Bearer"

        # Rotation revoked the old row: its access token dies with it, and the
        # old refresh token cannot be replayed (single invalid_grant).
        assert oauth.resolve_access_token(first["access_token"]) is None
        replay = _refresh(first["refresh_token"])
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"
        # The rotated pair works.
        assert oauth.resolve_access_token(new["access_token"]) is not None

    def test_refresh_token_of_another_client_is_invalid_grant(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        other = client.post(
            "/oauth/register",
            json={
                "client_name": "Other Connector",
                "redirect_uris": ["https://other.example/cb"],
            },
        ).json()
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": other["client_id"],
                "client_secret": other["client_secret"],
                "refresh_token": tokens["refresh_token"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"

    def test_unsupported_grant_type_is_named(
        self, client: TestClient, oauth_db: DatabaseManager, registered: dict
    ) -> None:
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "password",
                "client_id": registered["client_id"],
                "client_secret": registered["client_secret"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"

    def test_client_auth_method_confusion_is_refused(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        """A public client cannot present a secret; secretless != confidential."""
        import base64

        public = client.post(
            "/oauth/register",
            json={
                "client_name": "Public Connector",
                "redirect_uris": ["https://pub.example/cb"],
                "token_endpoint_auth_method": "none",
            },
        ).json()
        assert "client_secret" not in public
        code, verifier = _authorize_and_get_code(client, public)
        # Presenting a fabricated Basic credential must not authenticate a
        # client registered as token_endpoint_auth_method=none.
        basic = base64.b64encode(
            f"{public['client_id']}:zolai_cs_fabricated".encode()
        ).decode()
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "redirect_uri": public["redirect_uris"][0],
                "code": code,
                "code_verifier": verifier,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
            },
        )
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"

    def test_confidential_client_authenticates_via_basic(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
    ) -> None:
        import base64
        from urllib.parse import quote

        registered = client.post(
            "/oauth/register",
            json={
                "client_name": "Confidential Connector",
                "redirect_uris": ["https://conf.example/cb"],
                "token_endpoint_auth_method": "client_secret_basic",
            },
        ).json()
        secret = registered["client_secret"]
        assert secret.startswith(oauth.CLIENT_SECRET_PREFIX)

        code, verifier = _authorize_and_get_code(client, registered)
        # RFC 6749 §2.3.1: id and secret are form-urlencoded before base64.
        cred = base64.b64encode(
            f"{quote(registered['client_id'], safe='')}:"
            f"{quote(secret, safe='')}".encode()
        ).decode()
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "redirect_uri": registered["redirect_uris"][0],
                "code": code,
                "code_verifier": verifier,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {cred}",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"].startswith(oauth.ACCESS_TOKEN_PREFIX)

        # Wrong secret for the same client → invalid_client, never a token.
        bad = base64.b64encode(
            f"{registered['client_id']}:{oauth.CLIENT_SECRET_PREFIX}wrong".encode()
        ).decode()
        denied = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": "zolai_rt_x"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {bad}",
            },
        )
        assert denied.status_code == 401
        assert denied.json()["error"] == "invalid_client"

    def test_token_requires_urlencoded_form(
        self, client: TestClient, oauth_db: DatabaseManager
    ) -> None:
        response = client.post("/oauth/token", json={"grant_type": "authorization_code"})
        assert response.status_code == 415
        assert response.json()["error"] == "unsupported_media_type"

    def test_disabled_user_loses_every_oauth_grant(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        """The identity layer stays the kill switch — no second one."""
        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        assert oauth.resolve_access_token(tokens["access_token"]) is not None

        session_auth.set_user_enabled("founder", enabled=False, actor="test")
        assert oauth.resolve_access_token(tokens["access_token"]) is None

        session_auth.set_user_enabled("founder", enabled=True, actor="test")
        assert oauth.resolve_access_token(tokens["access_token"]) is not None

    def test_expired_or_revoked_access_token_resolves_to_none(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        with oauth_db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE oauth_tokens SET access_expires_at = '2000-01-01 00:00:00' "
                    "WHERE access_token_hash = :h"
                ),
                {"h": oauth.hash_token(tokens["access_token"])},
            )
        assert oauth.resolve_access_token(tokens["access_token"]) is None

        with oauth_db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE oauth_tokens SET access_expires_at = :future, "
                    "revoked_at = :now WHERE access_token_hash = :h"
                ),
                {
                    "future": oauth._now_plus(3600),
                    "now": oauth._stamp(),
                    "h": oauth.hash_token(tokens["access_token"]),
                },
            )
        assert oauth.resolve_access_token(tokens["access_token"]) is None
        # A foreign prefix is never a credential here.
        assert oauth.resolve_access_token("zolai_ss_something") is None
        assert oauth.resolve_access_token(None) is None

    def test_resolve_updates_last_used_and_is_prefix_disciplined(
        self,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        record = oauth.resolve_access_token(tokens["access_token"])
        assert record is not None
        with oauth_db.engine.connect() as conn:
            last_used = conn.execute(
                text("SELECT last_used_at FROM oauth_tokens"),
            ).scalar()
        assert last_used is not None
        # Key/display prefix discipline: never enough to reconstruct.
        assert record["key_prefix"] == tokens["access_token"][:16]


class TestMiddlewareBearerPath:
    """The middleware edit: ``Bearer zolai_at_*`` → principal + narrowed tier."""

    @pytest.fixture()
    def guarded_client(
        self, oauth_db: DatabaseManager, monkeypatch: pytest.MonkeyPatch
    ) -> TestClient:
        """One lifespan-free app: OAuth routes (ungated passthrough) + a
        gated probe route behind ``ApiKeyMiddleware`` in **enforce** mode, so
        the tier is actually enforced (warn would dual-accept and hide a
        broken resolve)."""
        from zolai.api.auth_middleware import ApiKeyMiddleware

        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        application = FastAPI()
        application.include_router(oauth_router.router)
        application.add_middleware(ApiKeyMiddleware)

        @application.get("/api/v1/records/probe")
        def _probe(request: Request) -> dict[str, Any]:
            record = getattr(request.state, "api_key", None)
            oauth_state = getattr(request.state, "oauth", None)
            return {
                "authenticated": record is not None,
                "scopes": list((record or {}).get("scopes") or []),
                "auth_source": (record or {}).get("auth_source"),
                "oauth_identity": oauth_state,
                "has_session_state": hasattr(request.state, "session"),
            }

        client = TestClient(application)
        reset_rate_limiter()
        yield client
        reset_rate_limiter()

    def test_bearer_access_token_resolves_through_the_middleware(
        self,
        guarded_client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(guarded_client, registered)
        tokens = _exchange_code(guarded_client, registered, code, verifier).json()

        response = guarded_client.get(
            "/api/v1/records/probe",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["authenticated"] is True
        assert body["auth_source"] == "oauth"
        assert sorted(body["scopes"]) == sorted(session_auth.ADMIN_SCOPES)
        assert body["oauth_identity"] == {
            "user_id": user_db["id"],
            "username": "founder",
            "client_id": registered["client_id"],
        }
        # An OAuth grant is NOT a session: logout must never see one.
        assert body["has_session_state"] is False

    def test_expired_grant_falls_through_to_the_401_path(
        self,
        guarded_client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        code, verifier = _authorize_and_get_code(guarded_client, registered)
        tokens = _exchange_code(guarded_client, registered, code, verifier).json()
        with oauth_db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE oauth_tokens SET access_expires_at = '2000-01-01 00:00:00' "
                    "WHERE access_token_hash = :h"
                ),
                {"h": oauth.hash_token(tokens["access_token"])},
            )
        response = guarded_client.get(
            "/api/v1/records/probe",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        # Enforce: an unresolvable zolai_at_* is an ordinary missing key.
        assert response.status_code == 401
        assert response.json()["detail"]["error"] == "unauthorized"

    def test_an_api_key_still_takes_the_unchanged_key_path(
        self,
        guarded_client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
    ) -> None:
        """Regression: the OAuth branch must not shadow the key path."""
        from zolai.api import auth as api_auth

        plaintext = api_auth.generate_api_key()
        with oauth_db.engine.begin() as conn:
            from zolai.api.auth import hash_key

            conn.execute(
                text(
                    "INSERT INTO api_keys (name, key_prefix, key_hash, scopes, "
                    " created_by) VALUES (:n, :p, :h, :s, 0)"
                ),
                {
                    "n": "probe-key",
                    "p": plaintext[:16],
                    "h": hash_key(plaintext),
                    "s": '["dataset:read"]',
                },
            )
        api_auth.invalidate_key_cache()
        response = guarded_client.get(
            "/api/v1/records/probe",
            headers={"X-API-Key": plaintext},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["auth_source"] is None  # key path, not oauth
        assert body["scopes"] == ["dataset:read"]
        assert body["oauth_identity"] is None


# ---------------------------------------------------------------------------
# C5 — the strict WWW-Authenticate challenge (flag-gated, DEFAULT OFF)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Real MCP transports + OAuth routes behind ``ApiKeyMiddleware``.

    Lifespan-free (never ``create_app``): ``tools/list`` needs no DB, and the
    OAuth/session seams come from the ``oauth_db``/``user_db`` fixtures.
    ``ZOLAI_API_AUTH`` is pinned to ``warn`` — the shipped default — so the
    flag under test is the only variable.
    """
    from zolai.api.auth_middleware import ApiKeyMiddleware
    from zolai.api.mcp_router import root_router as mcp_root_router
    from zolai.api.mcp_router import router as mcp_router

    monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
    application = FastAPI()
    application.include_router(oauth_router.router)
    application.include_router(mcp_router)
    application.include_router(mcp_root_router)
    application.add_middleware(ApiKeyMiddleware)
    return application


@pytest.fixture()
def mcp_client(mcp_app: FastAPI) -> TestClient:
    return TestClient(mcp_app)


_TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


class TestStrictChallenge:
    """``ZOLAI_MCP_OAUTH_REQUIRED`` — opt-in strict, default byte-identical.

    Parametrized over ``oauth.MCP_PATHS`` (the source of truth) so the
    middleware's two path literals cannot drift away from this constant
    without failing a gate.
    """

    @pytest.mark.parametrize("mcp_path", sorted(oauth.MCP_PATHS))
    def test_flag_off_keyless_mcp_still_serves_200(
        self,
        mcp_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        mcp_path: str,
    ) -> None:
        """Default-safe: Claude / Cursor / OpenCode / VS Code / Gemini CLI."""
        from zolai.agent.tools.registry import allow_list

        monkeypatch.delenv("ZOLAI_MCP_OAUTH_REQUIRED", raising=False)
        response = mcp_client.post(mcp_path, json=_TOOLS_LIST)
        assert response.status_code == 200, response.text
        assert "www-authenticate" not in response.headers, "no challenge by default"
        tools = response.json()["result"]["tools"]
        # The anonymous tier, unchanged: the real handler answered with the
        # registry's public allow-list (8 today), not an error shell.
        assert len(tools) == len(allow_list("public"))
        assert len(tools) > 0

    @pytest.mark.parametrize("mcp_path", sorted(oauth.MCP_PATHS))
    def test_strict_flag_challenges_keyless_mcp_with_absolute_resource_metadata(
        self,
        mcp_client: TestClient,
        strict_flag: str,
        mcp_path: str,
    ) -> None:
        response = mcp_client.post(mcp_path, json=_TOOLS_LIST)
        assert response.status_code == 401, response.text
        header = response.headers["www-authenticate"]
        assert header.startswith("Bearer resource_metadata=")
        metadata_url = header.split('"')[1]
        assert metadata_url == (
            "http://testserver/.well-known/oauth-protected-resource"
        ), "resource_metadata must be an absolute URL of the RFC 9728 document"
        # No credential was presented → RFC 6750 forbids an error attribute.
        assert "error=" not in header
        assert response.json() == {
            "detail": {"error": "unauthorized", "reason": "oauth_required"},
        }

    def test_strict_flag_still_serves_discovery_without_a_credential(
        self, mcp_client: TestClient, strict_flag: str
    ) -> None:
        """Strict mode must not turn off the switch that turns it on."""
        for path in AS_PATHS:
            response = mcp_client.get(path)
            assert response.status_code == 200, f"{path} must stay public: {response.text}"
            assert "www-authenticate" not in response.headers, path
            assert response.json()["issuer"] == "http://testserver"
        for path in PRM_PATHS:
            response = mcp_client.get(path)
            assert response.status_code == 200, f"{path} must stay public: {response.text}"
            assert "www-authenticate" not in response.headers, path
            assert response.json()["resource"].startswith("http://testserver/")

    def test_strict_flag_serves_a_valid_bearer_unchanged(
        self,
        mcp_client: TestClient,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        strict_flag: str,
    ) -> None:
        """A resolved OAuth principal is never challenged — only absence is."""
        from zolai.agent.tools.registry import allow_list

        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        response = mcp_client.post(
            "/mcp",
            json=_TOOLS_LIST,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 200, response.text
        assert "www-authenticate" not in response.headers
        tools = response.json()["result"]["tools"]
        # Admin user via rbac.role_for — strictly wider than the anonymous set.
        assert len(tools) == len(allow_list("admin"))
        assert len(tools) > len(allow_list("public"))

    def test_strict_flag_rejects_an_expired_bearer_with_invalid_token(
        self,
        mcp_client: TestClient,
        client: TestClient,
        oauth_db: DatabaseManager,
        user_db: dict,
        registered: dict,
        strict_flag: str,
    ) -> None:
        """A presented-but-dead zolai_at_* gets RFC 6750 ``error``."""
        code, verifier = _authorize_and_get_code(client, registered)
        tokens = _exchange_code(client, registered, code, verifier).json()
        with oauth_db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE oauth_tokens SET access_expires_at = '2000-01-01 00:00:00' "
                    "WHERE access_token_hash = :h"
                ),
                {"h": oauth.hash_token(tokens["access_token"])},
            )
        response = mcp_client.post(
            "/mcp",
            json=_TOOLS_LIST,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 401
        header = response.headers["www-authenticate"]
        assert header.startswith("Bearer resource_metadata=")
        assert 'error="invalid_token"' in header
        assert "error_description=" in header
