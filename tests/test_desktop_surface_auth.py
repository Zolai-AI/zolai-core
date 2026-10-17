"""The ``/desktop`` surface must never serve an anonymous caller (T1/T2).

``zolai/api/jsonl_router.py`` shipped with **no** ``require_scope`` dependency,
so under ``ZOLAI_API_AUTH=enforce`` (the production mode) all seven JSONL
import/export routes — including the full-corpus write
(``POST /import/all``) and the full-corpus read (``POST /export/all``) — were
reachable with no credential at all.  ``zolai/api/desktop_router.py`` was
already guarded with ``require_scope("dataset:read")``; the JSONL half of the
same ``/desktop`` prefix was not.

Two properties are pinned here:

1. **Anonymous is 401 on every ``/desktop`` route.**  The route list is read
   from ``desktop_router.routes`` + ``jsonl_router.routes`` *at runtime*, so a
   route added later is covered automatically instead of by a hand-kept list.
2. **Reads and writes do not share a scope.**  The three GETs keep the reading
   scope every other ``/desktop`` read already uses; the four POSTs (two
   full-corpus writes/reads) need a stricter, *different* action drawn from the
   frozen 33-action vocabulary in ``zolai/api/auth.py``.

Route **class** declarations (member, never ``unclassified``) live in
``zolai/api/rbac.py`` and are guarded by
``tests/test_rbac_public_matrix.py``.

The scope constants are read off the module at call time (not imported at
import time) so that this file still *runs* against the unfixed router and
fails on a real assertion instead of a collection error.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from zolai.api import auth, rbac
from zolai.api import jsonl_router as _jsonl_router
from zolai.api.desktop_router import router as desktop_router
from zolai.api.jsonl_router import router as jsonl_router
from zolai.api.server import create_app

#: Methods that never carry a body/params worth asserting on.
_SKIP_METHODS = frozenset({"HEAD", "OPTIONS"})


def _declared_scopes() -> tuple[str, str]:
    """The router's declared ``(read, write)`` scopes, asserted to exist."""
    read = getattr(_jsonl_router, "READ_SCOPE", None)
    write = getattr(_jsonl_router, "WRITE_SCOPE", None)
    assert read, "jsonl_router must declare READ_SCOPE"
    assert write, "jsonl_router must declare WRITE_SCOPE"
    return read, write


def _desktop_routes() -> dict[tuple[str, str], object]:
    """Every ``(method, path)`` served by both ``/desktop`` routers."""
    found: dict[tuple[str, str], object] = {}
    for router in (desktop_router, jsonl_router):
        for route in router.routes:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if not path or not methods or not path.startswith("/desktop"):
                continue
            for method in methods:
                if method.upper() in _SKIP_METHODS:
                    continue
                found[(method.upper(), path)] = route
    return found


def _declared_actions(route) -> set[str]:
    """Frozen vocabulary actions named by a route's dependency closures.

    ``auth.require_scope(action)`` closes over ``action``, so a dependency that
    calls it is inspected by reading its closure cells.  Only strings that are
    real members of ``VALID_ACTIONS`` are collected, so unrelated closure
    values can never make this assertion pass by accident.
    """
    actions: set[str] = set()

    def walk(dependant) -> None:
        closure = getattr(getattr(dependant, "call", None), "__closure__", None) or ()
        for cell in closure:
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover - empty cell
                continue
            if isinstance(value, str) and value in auth.VALID_ACTIONS:
                actions.add(value)
        for sub in getattr(dependant, "dependencies", None) or []:
            walk(sub)

    for dependant in getattr(getattr(route, "dependant", None), "dependencies", None) or []:
        walk(dependant)
    return actions


# ---------------------------------------------------------------------------
# Non-vacuity: the surface this guard walks is real
# ---------------------------------------------------------------------------


class TestSurfaceIsEnumerated:
    def test_desktop_routes_are_found_from_the_routers(self) -> None:
        routes = _desktop_routes()
        # Not a magic number: it only has to be big enough that an empty /
        # mis-scoped enumeration cannot pass. 56 HTTP routes today.
        assert len(routes) >= 40, f"only found {len(routes)} desktop routes"
        paths = {path for _, path in routes}
        assert "/desktop/stats" in paths
        assert "/desktop/bible/verses" in paths
        # Both routers contribute, otherwise the jsonl half is never checked.
        assert "/desktop/jsonl/tables" in paths
        assert "/desktop/jsonl/export/all" in paths

    def test_jsonl_surface_matches_the_reported_hole(self) -> None:
        jsonl_paths = {
            path for _, path in _desktop_routes() if path.startswith("/desktop/jsonl")
        }
        assert jsonl_paths == {
            "/desktop/jsonl/import/all",
            "/desktop/jsonl/import/file",
            "/desktop/jsonl/export/all",
            "/desktop/jsonl/export/table",
            "/desktop/jsonl/import/log",
            "/desktop/jsonl/import/status/{batch_id}",
            "/desktop/jsonl/tables",
        }


# ---------------------------------------------------------------------------
# T1 — anonymous is rejected everywhere under enforce
# ---------------------------------------------------------------------------


class TestAnonymousIs401UnderEnforce:
    def test_every_desktop_route_401s_anonymous(self, monkeypatch) -> None:
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        routes = _desktop_routes()
        client = TestClient(create_app())

        not_gated: list[str] = []
        for method, path in sorted(routes):
            resp = client.request(method, path)
            if resp.status_code != 401:
                not_gated.append(f"{method} {path} -> {resp.status_code}")

        assert not_gated == [], (
            f"anonymous callers reached {len(not_gated)} /desktop routes under enforce: "
            f"{not_gated}"
        )

    def test_the_jsonl_write_routes_are_gated(self, monkeypatch) -> None:
        """The full-corpus routes named in the report, spelled out."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
        client = TestClient(create_app())
        for method, path in (
            ("POST", "/desktop/jsonl/import/all"),
            ("POST", "/desktop/jsonl/import/file"),
            ("POST", "/desktop/jsonl/export/all"),
            ("POST", "/desktop/jsonl/export/table"),
            ("GET", "/desktop/jsonl/tables"),
            ("GET", "/desktop/jsonl/import/log"),
            ("GET", "/desktop/jsonl/import/status/batch-1"),
        ):
            resp = client.request(method, path, json={} if method == "POST" else None)
            assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"

    def test_warn_mode_still_dual_accepts(self, monkeypatch) -> None:
        """``warn`` keeps today's posture: no regression for a legacy caller."""
        monkeypatch.setenv("ZOLAI_API_AUTH", "warn")
        client = TestClient(create_app())
        # Passes the scope dependency (anonymous dual-accept) — whatever the
        # handler then does, it must not be a 401.
        resp = client.get("/desktop/jsonl/import/log")
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# T1 — reads and writes do not share a scope
# ---------------------------------------------------------------------------


class TestReadWriteScopeSplit:
    def test_scopes_are_distinct_and_in_the_frozen_vocabulary(self) -> None:
        read, write = _declared_scopes()
        assert read in auth.VALID_ACTIONS
        assert write in auth.VALID_ACTIONS
        assert read != write
        # Reads keep the scope every other /desktop read already uses.
        assert read == "dataset:read"
        # The write side is strictly more privileged in intent than a read.
        assert not write.endswith(":read")

    def test_jsonl_reads_require_the_reading_scope(self) -> None:
        read, _ = _declared_scopes()
        routes = _desktop_routes()
        for path in (
            "/desktop/jsonl/tables",
            "/desktop/jsonl/import/log",
            "/desktop/jsonl/import/status/{batch_id}",
        ):
            route = routes[("GET", path)]
            assert _declared_actions(route) == {read}, path

    def test_jsonl_writes_require_the_stricter_scope(self) -> None:
        _, write = _declared_scopes()
        routes = _desktop_routes()
        for path in (
            "/desktop/jsonl/import/all",
            "/desktop/jsonl/import/file",
            "/desktop/jsonl/export/table",
            "/desktop/jsonl/export/all",
        ):
            route = routes[("POST", path)]
            assert _declared_actions(route) == {write}, path

    def test_no_jsonl_route_is_left_unguarded(self) -> None:
        """No route on this router may be scope-free, whatever its verb."""
        read, write = _declared_scopes()
        routes = _desktop_routes()
        jsonl = {k: v for k, v in routes.items() if k[1].startswith("/desktop/jsonl")}
        assert jsonl, "jsonl routes disappeared — the enumeration is broken"
        for (method, path), route in jsonl.items():
            actions = _declared_actions(route)
            assert actions, f"{method} {path} has no require_scope dependency"
            assert actions in ({read}, {write}), (
                f"{method} {path} declares {sorted(actions)}, expected exactly one of "
                f"the declared read/write scopes"
            )

    def test_desktop_router_keeps_its_existing_read_scope(self) -> None:
        """Untouched by this change: the non-jsonl half still reads on dataset:read."""
        routes = _desktop_routes()
        assert _declared_actions(routes[("GET", "/desktop/stats")]) == {"dataset:read"}


# ---------------------------------------------------------------------------
# T2 — /desktop is a declared member surface, never unclassified
# ---------------------------------------------------------------------------


class TestDesktopClassification:
    def test_desktop_routes_classify_as_member(self) -> None:
        for method, path in _desktop_routes():
            assert rbac.classify_route(method, path) == "member", f"{method} {path}"

    def test_representative_desktop_paths(self) -> None:
        for method, path in (
            ("GET", "/desktop/bible/verses"),
            ("GET", "/desktop/jsonl/tables"),
            ("POST", "/desktop/jsonl/import/all"),
            ("POST", "/desktop/jsonl/export/all"),
            ("GET", "/desktop/jsonl/import/log"),
        ):
            assert rbac.classify_route(method, path) == "member", f"{method} {path}"
