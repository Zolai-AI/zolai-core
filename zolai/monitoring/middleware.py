"""Pure-ASGI HTTP metrics middleware.

The route label is taken from the matched **route template**
(``scope["route"].path`` — e.g. ``/api/items/{id}``), never from the raw
request path, so label cardinality stays bounded and no user-controlled
data can leak into the metric labels.

Implemented as raw ASGI (not ``BaseHTTPMiddleware``) so streaming responses
and WebSockets are not buffered or rewritten.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from .metrics import HTTP_IN_FLIGHT, observe_request

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

#: Route label used when nothing matched (never the raw path).
UNMATCHED_ROUTE = "unmatched"


def route_template(scope: Scope) -> str:
    """Return the matched route template, or ``unmatched``.

    ``scope["route"]`` is populated by Starlette during routing, which always
    happens before the handler runs — so it is available by the time the
    response completes.  Raw paths are never used.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


class MetricsMiddleware:
    """ASGI middleware recording request counts, latency and in-flight gauge."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        status = 500
        started = time.perf_counter()
        HTTP_IN_FLIGHT.inc()

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                try:
                    status = int(message.get("status", 500))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    status = 500
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - started
            HTTP_IN_FLIGHT.dec()
            observe_request(
                method=str(scope.get("method", "GET")),
                route=route_template(scope),
                status=status,
                duration=duration,
            )
