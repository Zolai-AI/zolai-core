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

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zolai.api import auth, oauth_router
from zolai.auth import oauth

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
