"""One version string everywhere — core#13.

Three release literals used to disagree across the same backend:

======  =========================  ===================================
Value   Where it was reported      Now
======  =========================  ===================================
``0.3.0``   ``GET /`` fallback     ``zolai.__version__``
``1.0.0``   ``/health``, OpenAPI,  ``zolai.__version__``
            MCP ``serverInfo``
``2.0.0``   package + desktop /    canonical source
            dictionary apps,
            build info
======  =========================  ===================================

Guarded here from three sides: the package metadata (``pyproject.toml``)
agrees with ``zolai.__version__``, every HTTP/protocol surface reports that
value, and no API file may carry a release literal of its own again.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import zolai
from zolai.api import auth
from zolai.api.server import create_app
from zolai.config import Paths, config
from zolai.data.database import DatabaseManager
from zolai.data.migrations import create_api_keys_table
from zolai.mcp import protocol

_ROOT = Path(__file__).resolve().parent.parent

#: Files that report a release version — none of them may hardcode one.
_VERSION_SURFACE_FILES = (
    "zolai/api/server.py",
    "zolai/api/desktop_app.py",
    "zolai/api/dictionary_api.py",
    "zolai/mcp/protocol.py",
    "zolai/cli/main.py",
)

_RELEASE_LITERALS = ('"0.3.0"', '"1.0.0"', '"2.0.0"', "'0.3.0'", "'1.0.0'", "'2.0.0'", "v1.0.0")


@pytest.fixture()
def client() -> TestClient:
    """Main app without lifespan — routes do not need startup migrations."""
    return TestClient(create_app())


@pytest.fixture(scope="module")
def _ops_admin_key(tmp_path_factory) -> Iterator[str]:
    """Admin key on a throwaway auth DB for the gated ops surface."""
    mgr = DatabaseManager(f"sqlite:///{tmp_path_factory.mktemp('auth') / 'auth.db'}")
    mgr.init_db()
    create_api_keys_table(mgr)
    original = auth._get_manager
    auth._get_manager = lambda: mgr
    try:
        yield auth.create_api_key(name="ops-admin", scopes=["apikey:manage"])["plaintext"]
    finally:
        auth._get_manager = original
        mgr.dispose()


@pytest.fixture()
def ops_client(_ops_admin_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Lifespan runs so the sampler records build info, plus the admin key.
    Points at a scratch DB so migrations don't touch the canonical store."""
    scratch = tmp_path / "api_version_test.db"
    monkeypatch.setattr(config.paths, "db", scratch)
    test_client = TestClient(create_app())
    with test_client:
        test_client.headers.update({"X-API-Key": _ops_admin_key})
        yield test_client


class TestSingleSourceOfTruth:
    def test_pyproject_project_version_is_zolai_version(self) -> None:
        data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["project"]["version"] == zolai.__version__

    def test_no_release_literal_left_in_a_version_surface(self) -> None:
        """A second literal is exactly how the three values drifted apart."""
        for rel in _VERSION_SURFACE_FILES:
            source = (_ROOT / rel).read_text(encoding="utf-8")
            for literal in _RELEASE_LITERALS:
                assert literal not in source, f"{rel} still carries {literal}"

    def test_mcp_server_info_uses_it(self) -> None:
        assert protocol.SERVER_VERSION == zolai.__version__


class TestReportedOverHttp:
    def test_health_and_api_health_agree_with_the_package(
        self, client: TestClient
    ) -> None:
        for path in ("/health", "/api/v1/health"):
            body = client.get(path)
            assert body.status_code == 200, body.text
            assert body.json()["version"] == zolai.__version__, path

    def test_openapi_info_agrees_with_the_package(self, client: TestClient) -> None:
        assert create_app().openapi()["info"]["version"] == zolai.__version__

    def test_metrics_build_info_agrees_with_the_package(
        self, ops_client: TestClient
    ) -> None:
        """``/api/metrics/*`` is admin-gated (2026-10-21 ops surface), so the
        version read goes through a throwaway key rather than expecting 200."""
        body = ops_client.get("/api/metrics/info")
        assert body.status_code == 200, body.text
        assert body.json()["version"] == zolai.__version__

    def test_root_json_fallback_reports_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ``GET /`` fallback used to answer its own ``0.3.0``.

        ``config.paths.frontend`` is a read-only property, so the class seam is
        patched to a directory with no ``index.html`` — the JSON branch.
        """
        monkeypatch.setattr(Paths, "frontend", property(lambda self: tmp_path))
        body = TestClient(create_app()).get("/")
        assert body.status_code == 200, body.text
        assert body.headers["content-type"].startswith("application/json")
        payload = body.json()
        assert payload["version"] == zolai.__version__
        assert payload["api"] == "running"

    def test_mcp_initialize_reports_it(self) -> None:
        response = protocol.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            allow=frozenset(),
            call_tool=lambda *args, **kwargs: None,
        )
        assert response is not None
        assert response["result"]["serverInfo"]["version"] == zolai.__version__

    def test_desktop_health_reports_it(self) -> None:
        from zolai.api.desktop_app import create_desktop_app

        body = TestClient(create_desktop_app()).get("/health")
        assert body.status_code == 200, body.text
        assert body.json()["version"] == zolai.__version__


def test_config_paths_frontend_is_a_property() -> None:
    """Sanity on the seam the root-route test patches (a setter would mask it)."""
    assert isinstance(getattr(Paths, "frontend", None), property)
    assert isinstance(config.paths.frontend, Path)
