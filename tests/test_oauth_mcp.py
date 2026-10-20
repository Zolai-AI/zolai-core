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

from zolai.api import auth, oauth_router
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
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_client_metadata", reason

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
        assert response.json()["detail"]["error"] == "invalid_client_metadata"

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
