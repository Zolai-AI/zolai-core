"""The unauthenticated ``/ws`` echo socket is gone (security T1/T3).

``zolai/api/server.py`` used to carry::

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await manager.connect(ws)
        while True:
            data = await ws.receive_json()
            await manager.broadcast({"echo": data})

Three things made that worse than a stray echo toy:

1. It sat at the **app root**, outside ``/api/v1``, so neither
   ``auth_middleware.is_protected_path`` (which only covers ``/api/v1`` +
   ``EXTRA_PROTECTED_PATHS``) nor ``rbac.classify_route`` ever covered it.
   ``ApiKeyMiddleware.dispatch`` returns early for every non-HTTP scope, so a
   WebSocket handshake bypassed the key gate entirely.
2. It was an **echo broadcast**: ``ConnectionManager.broadcast`` fans out to
   one process-wide ``connections`` list, so any anonymous caller who was
   connected received every other caller's payload verbatim. Cross-tenant by
   construction, not by accident.
3. It had **no consumer** — ``zolai-tauri``, ``zolai-web``, ``zolai-mcp-server``
   and ``zolai-explorer`` all grep clean for ``/ws`` / ``ws://`` /
   ``WebSocket(``.

The decision was **delete, not gate**: putting a key check in front of a
cross-tenant broadcast would have enshrined the bug behind a login.

``ConnectionManager`` / ``manager`` deliberately STAY — the crawl/clean/splits
completion broadcasts in ``server.py`` still use them, and the authenticated
learning socket owns its own manager under ``zolai/learning/websocket/``.  The
over-deletion guard below pins that ``/api/v1/learning/ws`` (admin-gated by
``_ws_authorize`` in ``learning_tools_router``) is not collateral damage.
"""

from __future__ import annotations

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from zolai.api.server import create_app

#: The socket that was deleted.
DEAD_PATH = "/ws"

#: The socket that must survive the deletion (separately, correctly gated).
SURVIVING_PATH = "/api/v1/learning/ws"


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def client(app):
    # No ``with``: entering the lifespan starts the startup guard, which samples
    # the multi-GB DB. Nothing under test needs app state.
    return TestClient(app)


def _paths(routes) -> set[str]:
    return {ctx.path for ctx in iter_route_contexts(routes)}


def test_ws_route_is_absent_from_route_table(app) -> None:
    """The decisive assertion: ``/ws`` is not a route any more."""
    assert DEAD_PATH not in _paths(app.routes), (
        f"{DEAD_PATH} is still registered — iter_route_contexts saw it"
    )


def test_no_websocket_route_sits_at_the_app_root(app) -> None:
    """Root-level sockets are gone by *class*, not just by that one name.

    A future sibling at e.g. ``/socket`` would reintroduce the same hole, so
    the guard is written against the shape (a WebSocket route outside
    ``/api/v1``) rather than against the single literal.
    """
    root_sockets = [
        r.path
        for r in app.routes
        if "WebSocket" in type(r).__name__
        and not r.path.startswith("/api/v1")
    ]
    assert root_sockets == [], f"unauthenticated root websocket(s) returned: {root_sockets}"


def test_ws_handshake_is_refused(client) -> None:
    """A live handshake attempt no longer succeeds."""
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - starlette raises
        with client.websocket_connect(DEAD_PATH):
            pass
    # No handler means the router has no scope to accept on, so the connection
    # is closed during/right after the handshake instead of being echoed.
    assert "accepted" not in str(excinfo.value).lower()


def test_ws_is_not_echoed_back(client) -> None:
    """Nothing answers with ``{"echo": ...}`` any more.

    Written as a bare ``pytest.raises`` around the whole exchange: on the
    pre-fix source the connect, the send and the echoed receive all succeed, so
    no exception is raised and this test genuinely fails instead of passing on a
    half-dead socket.
    """
    with pytest.raises(Exception):  # noqa: B017
        with client.websocket_connect(DEAD_PATH) as sock:
            sock.send_json({"tenant": "A"})
            sock.receive_json()


def test_authenticated_learning_socket_is_not_collateral_damage() -> None:
    """``/api/v1/learning/ws`` is declared by its own router and must remain so.

    Pinned at the *router* rather than on the assembled app: ``server.py``
    reaches the socket through ``include_router``, and whether the assembled
    app lists a WebSocket route depends on router wiring, not on this change.
    The router declaration is what the rest of the codebase mounts.
    """
    import zolai.api.server as server_mod
    from zolai.api import learning_tools_router as ltr

    declared = {r.path for r in ltr.router.routes if "WebSocket" in type(r).__name__}
    assert SURVIVING_PATH in declared, (
        f"the admin-gated learning socket vanished from its router: {declared}"
    )
    assert not hasattr(server_mod, "WebSocketDisconnect"), (
        "server.py kept a now-dead WebSocketDisconnect import after /ws was removed"
    )
