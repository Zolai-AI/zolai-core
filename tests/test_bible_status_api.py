"""``GET /bible/status`` reports the database, not an empty directory — core#22.

The endpoint counted on-disk ``*.xml`` bundles under ``data/knowledge/bibles``.
On a database-backed deployment that directory does not exist, so the route
answered ``bible_files: 0`` while ``bible_verses`` held all 31,102 rows —
an availability signal that lied.  It now reports the row count (``verses``)
and an ``available`` flag, keeping ``bible_files`` as what it actually is: the
XML bundle count.

The manager seam is monkeypatched, so this runs against a fake manager: no
canonical DB, no network, and a broken DB still answers 200 (a status endpoint
must never 500).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from zolai.api import auth
from zolai.api.auth_middleware import reset_rate_limiter
from zolai.api.server import create_app


@pytest.fixture(autouse=True)
def _clean_auth_state() -> Iterator[None]:
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()
    yield
    auth.invalidate_key_cache()
    auth.reset_failure_log()
    reset_rate_limiter()


@pytest.fixture()
def client() -> TestClient:
    """Main app without lifespan — routes do not need startup migrations."""
    return TestClient(create_app())


class _ManagerWithAVerseCount:
    """Stand-in for the singleton the route resolves on every request."""

    def __init__(self, verses: int = 31_102) -> None:
        self.verses = verses

    def count(self, table_name: str) -> int:
        if table_name != "bible_verses":
            raise AssertionError(f"unexpected table asked for: {table_name}")
        return self.verses


class _BrokenManager:
    def count(self, table_name: str) -> int:
        raise RuntimeError("database is locked")


def _patch_manager(monkeypatch: pytest.MonkeyPatch, manager: object) -> None:
    # The route resolves ``get_manager`` *inside* the handler (local import),
    # so patching the module attribute is exactly the seam it uses.
    monkeypatch.setattr("zolai.data.database.get_manager", lambda *a, **k: manager)


class TestBibleStatus:
    def test_reports_the_db_verse_count(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_manager(monkeypatch, _ManagerWithAVerseCount())
        body = client.get("/bible/status")
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["status"] == "ok"
        assert payload["verses"] == 31_102, "core#22: the row count is the availability signal"
        assert payload["available"] is True
        # No XML bundle on a DB-backed host — reported honestly, next to the rows.
        assert payload["bible_files"] == 0
        assert set(payload) >= {"status", "bible_files", "verses", "available", "path"}

    def test_available_is_false_when_there_is_nothing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_manager(monkeypatch, _ManagerWithAVerseCount(0))
        payload = client.get("/bible/status").json()
        assert payload["verses"] == 0
        assert payload["available"] is False
        assert payload["status"] == "ok", "the endpoint itself is healthy"

    def test_a_broken_db_still_answers_200(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """A status probe must degrade, not 500 (the pre-fix shape had no DB read)."""
        _patch_manager(monkeypatch, _BrokenManager())
        body = client.get("/bible/status")
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["verses"] == 0
        assert payload["available"] is False
