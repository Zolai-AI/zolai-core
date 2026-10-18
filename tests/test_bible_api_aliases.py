"""``/api/v1/bible/*`` aliases their ``/desktop/bible/*`` counterparts.

Three properties are pinned:

1. **Identity** — every alias is registered against the *same handler object*
   as the desktop route it mirrors. Comparing ``route.endpoint`` proves there
   is no copy-paste reimplementation to drift.
2. **Parity** — the alias answers with byte-equal JSON to its counterpart for
   representative reads.
3. **No regression** — the ``/desktop`` surface is still exactly 51 routes,
   and every alias is still gated by ``dataset:read`` (401 anonymous under
   ``enforce``), so nothing was widened on the way to a versioned path.

No database writes: every route exercised here is a read.
"""

from __future__ import annotations

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from zolai.api import rbac
from zolai.api.bible_alias_router import ALIAS_MAP, ALIAS_PREFIX, SOURCE_PREFIX
from zolai.api.desktop_router import router as desktop_router
from zolai.api.server import create_app

#: Read-only probes, chosen to cover a live DB read, a script-backed route and
#: a nested path. ``?book=GEN&chapter=1`` is chapter 1 of Genesis.
PROBES = (
    "/bible/books",
    "/bible/chapters?book=GEN",
    "/bible/verses?book=GEN&chapter=1",
    "/bible/context/topics",
)


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app)


def _routes(router, prefix: str) -> dict[str, object]:
    return {
        r.path: r
        for r in router.routes
        if (getattr(r, "path", "") or "").startswith(prefix + "/")
    }


def test_alias_map_is_not_empty() -> None:
    assert ALIAS_MAP, "no /desktop/bible routes were aliased — did they move?"
    for src, dst in ALIAS_MAP.items():
        assert src.startswith(SOURCE_PREFIX), src
        assert dst.startswith(ALIAS_PREFIX), dst


def _served(app) -> dict[str, object]:
    """Path -> served context, recursing into included routers.

    FastAPI 0.143 keeps ``_IncludedRouter`` wrappers in ``app.routes``, so a
    bare ``for r in app.routes`` silently under-counts. ``iter_route_contexts``
    is the supported walk.
    """
    return {ctx.path: ctx for ctx in iter_route_contexts(app.routes)}


def test_every_desktop_bible_route_has_exactly_one_alias(app) -> None:
    source = _routes(desktop_router, SOURCE_PREFIX)
    assert len(source) == 8, f"desktop bible route count moved: {sorted(source)}"

    registered = _served(app)
    for path in source:
        alias_path = path.replace(SOURCE_PREFIX, ALIAS_PREFIX, 1)
        assert alias_path in registered, f"{path} has no registered alias {alias_path}"


def test_aliases_reuse_the_same_handler_objects(app) -> None:
    """Identity, not equality — a copy-paste re-implementation fails here."""
    source = _routes(desktop_router, SOURCE_PREFIX)
    served = _served(app)
    for path, route in source.items():
        alias = served[path.replace(SOURCE_PREFIX, ALIAS_PREFIX, 1)]
        assert alias.endpoint is route.endpoint, (
            f"{alias.path} does not delegate to the /desktop handler"
        )
        assert sorted(alias.methods) == sorted(route.methods), alias.path


def test_alias_responses_match_the_desktop_routes(client) -> None:
    for probe in PROBES:
        desktop = client.get(SOURCE_PREFIX + probe)
        alias = client.get(ALIAS_PREFIX + probe)
        assert desktop.status_code == alias.status_code, probe
        assert alias.json() == desktop.json(), f"{probe} diverged from its alias"


def test_desktop_surface_is_unchanged(app) -> None:
    """The 51 ``/desktop`` routes must all still be there."""
    paths = [ctx.path for ctx in iter_route_contexts(app.routes)
             if ctx.path.startswith("/desktop")]
    assert len(paths) == 51, f"/desktop count is {len(paths)}, expected 51"


def test_every_alias_is_member_class_not_unclassified() -> None:
    """The completeness guard's invariant, checked at source."""
    for path in ALIAS_MAP.values():
        assert rbac.classify_route("GET", path) == "member", path
        assert rbac.is_public_path("GET", path) is False, path


def test_aliases_401_anonymous_under_enforce(monkeypatch) -> None:
    """The alias is gated exactly like the desktop route it mirrors.

    Same pattern (and same ``TestClient``-without-lifespan caveat) as
    ``tests/test_desktop_surface_auth.py``: no key must be a 401 under
    ``enforce``, which is only true if the ``dataset:read`` dependency came
    along with the aliased handler.
    """
    monkeypatch.setenv("ZOLAI_API_AUTH", "enforce")
    anon = TestClient(create_app())
    for probe in PROBES:
        resp = anon.get(ALIAS_PREFIX + probe)
        assert resp.status_code == 401, (probe, resp.status_code, resp.text[:200])
