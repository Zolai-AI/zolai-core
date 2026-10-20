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
from fastapi import FastAPI
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
    monkeypatch.setattr(oauth, "_get_manager", lambda: mgr)
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
