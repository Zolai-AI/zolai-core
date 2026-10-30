"""Guard: a route handler may not be ``async def`` unless it actually awaits.

An ``async def`` endpoint that performs synchronous work — SQLite, ``subprocess``,
numpy, ``httpx``-free file scans — runs *on the event loop*, so one request
freezes every other in-flight request. That is core#24's bug, and it turned out
to be systemic: ``/desktop/*`` shelled out with a 120s ``subprocess.run``
timeout inline, the legacy ``/learning/dictionary`` mount took 8.5s inline, and
43 more handlers were in the same shape.

FastAPI's contract is the fix: a ``def`` endpoint runs in the worker threadpool.
So the rule this test enforces is mechanical — every decorated endpoint must
either contain an ``await``/``async for``/``async with``, or be a plain ``def``.

``ALLOWLIST`` is only for endpoints that provably do no I/O (a dict of constants,
a static string, an immediate raise). Adding a name there is a claim that the
handler is instantaneous; the honest default is to make it ``def``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

API_DIR = pathlib.Path(__file__).resolve().parent.parent / "zolai" / "api"
ROUTER_METHODS = {"get", "post", "put", "patch", "delete", "websocket", "api_route"}

#: ``module.py -> endpoint names`` that stay ``async`` on purpose.
ALLOWLIST: dict[str, set[str]] = {
    # Liveness probes: pure in-memory reads, and they must not queue behind a
    # saturated threadpool.
    "server.py": {"lifespan", "health", "api_v1_health", "admin_health"},
    "desktop_app.py": {"health"},
    # Request/response plumbing that is genuinely async.
    "mcp_router.py": {
        "mcp_get_not_allowed",
        "mcp_delete_not_allowed",
        "mcp_root_get_not_allowed",
        "mcp_root_delete_not_allowed",
    },
    "oauth_router.py": {
        # RFC 8414 / 9728 / OIDC documents: constant metadata, no I/O.
        "well_known_oauth_authorization_server",
        "well_known_openid_configuration",
        "well_known_oauth_protected_resource",
        "well_known_oauth_protected_resource_mcp",
        "well_known_oauth_protected_resource_api_mcp",
        "mcp_relative_oauth_authorization_server",
        "mcp_relative_oauth_protected_resource",
        "api_mcp_relative_oauth_protected_resource",
        "oauth_authorize_css",
    },
}


def _endpoints(tree: ast.AST) -> list[tuple[int, str, bool, bool]]:
    """``(lineno, name, is_async, has_await)`` for every decorated endpoint."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        decorated = False
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ROUTER_METHODS
                and isinstance(func.value, ast.Name)
                and func.value.id in {"app", "router", "public_router", "admin_router"}
            ):
                decorated = True
                break
        if not decorated:
            continue
        has_await = any(
            isinstance(child, ast.Await | ast.AsyncFor | ast.AsyncWith)
            for child in ast.walk(node)
        )
        found.append((node.lineno, node.name, isinstance(node, ast.AsyncFunctionDef), has_await))
    return found


@pytest.mark.parametrize(
    "module_path",
    sorted(API_DIR.rglob("*.py")),
    ids=lambda p: p.name,
)
def test_sync_endpoints_do_not_run_on_the_event_loop(module_path: pathlib.Path) -> None:
    tree = ast.parse(module_path.read_text())
    allowed = ALLOWLIST.get(module_path.name, set())
    offenders = [
        f"{module_path.name}:{lineno} {name}"
        for lineno, name, is_async, has_await in _endpoints(tree)
        if is_async and not has_await and name not in allowed
    ]
    assert not offenders, (
        "async endpoints without a single await block the event loop — make them "
        "`def` (threadpool) or offload their work with run_in_threadpool:\n"
        + "\n".join(offenders)
    )
