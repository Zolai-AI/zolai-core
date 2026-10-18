"""Versioned ``/api/v1/bible/*`` aliases for the ``/desktop/bible/*`` routes.

``/desktop/bible/*`` were the **only** Bible routes in the app, and the whole
prefix lives outside ``/api/v1``. That is a real problem for the MCP layer and
for any consumer that wants a versioned surface: Bible access is central
(Zolai's parallel corpus *is* the Bible), and MCP has to be able to name a
stable, versioned path.

This module adds ``/api/v1/bible/*`` as **pure aliases**. Nothing is moved,
renamed or reimplemented:

* each alias is registered against the **same handler callable object** as its
  ``/desktop/bible/*`` counterpart — not a copy of the function, not a
  re-declaration of its signature;
* ``/desktop/*`` stays byte-identical in behaviour and count;
* the alias router carries the **same** ``require_scope("dataset:read")``
  router-level dependency the desktop router carries, so the aliases are
  member-class and anonymous callers get 401 under ``enforce``.

Cloning is programmatic rather than hand-copied on purpose. FastAPI derives
every query parameter from ``inspect.signature(endpoint)``, so re-registering
the same callables reproduces the ``Query(...)`` defaults, the response model
and the docs exactly — with zero drift when ``desktop_router`` gains a Bible
route later. The parity test in ``tests/test_bible_api_aliases.py`` pins
endpoint *identity* (not just paths) so this cannot quietly rot into a
copy-paste reimplementation.

Deliberately **not** declared: a ``/api/v1/bible`` -> ``/api/v1/stats``
alias. ``/api/v1/foundation/stats`` is PUBLIC, so aliasing a member route onto
it would have widened a guarded read to anonymous. ``/desktop/stats`` keeps
its single home until that is designed on purpose.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .auth import require_scope
from .desktop_router import router as desktop_router

#: The prefix being aliased away from (never mutated).
SOURCE_PREFIX = "/desktop/bible"

#: The versioned prefix the aliases are published under.
ALIAS_PREFIX = "/api/v1/bible"

#: Same scope the desktop router already enforces — reads of corpus rows.
BIBLE_SCOPE = "dataset:read"


def _source_routes() -> list:
    """Every ``/desktop/bible*`` route object, in declaration order."""
    return [
        route
        for route in desktop_router.routes
        if (getattr(route, "path", "") or "").startswith(SOURCE_PREFIX + "/")
    ]


def build_router() -> APIRouter:
    """Assemble the alias router by re-registering the desktop handlers.

    Returns a fresh ``APIRouter``; call once and keep the result.
    """
    alias = APIRouter(
        prefix=ALIAS_PREFIX,
        tags=["bible"],
        dependencies=[Depends(require_scope(BIBLE_SCOPE))],
    )
    for route in _source_routes():
        suffix = route.path[len(SOURCE_PREFIX):] or "/"
        alias.add_api_route(
            path=suffix,
            endpoint=route.endpoint,  # the *same* function object
            methods=sorted(route.methods),
            response_model=route.response_model,
            status_code=route.status_code,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            tags=list(route.tags),
            deprecated=route.deprecated,
            include_in_schema=route.include_in_schema,
            name=route.name,
            dependencies=list(route.dependencies),
        )
    return alias


#: Module-level router for ``server.py`` to ``include_router``.
router = build_router()

#: ``(source path -> alias path)`` — read off the built router so the doc
#: table in ``README``/docs can never drift from the code.
ALIAS_MAP: dict[str, str] = {
    route.path: route.path.replace(SOURCE_PREFIX, ALIAS_PREFIX, 1)
    for route in _source_routes()
}

__all__ = ["ALIAS_MAP", "ALIAS_PREFIX", "BIBLE_SCOPE", "SOURCE_PREFIX", "build_router", "router"]
