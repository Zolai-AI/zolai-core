"""Tool primitives for the agent runtime (P3 §C).

Server-side only — the client never supplies a tool list; the registry is
the single source of truth and the executor enforces the caller's allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import re
from typing import Any

#: The grammar every **published** tool name must satisfy.
#:
#: One :class:`ToolSpec` feeds *two* wires: the OpenAI ``tools`` array — whose
#: ``function.name`` OpenAI documents as ``^[a-zA-Z0-9_-]{1,64}$``, i.e. a dot
#: is rejected — and the MCP ``tools/list`` entry, which accepts a dotted name.
#: A dotted catalogue name is therefore one the same spec can publish to MCP
#: and *cannot* publish to OpenAI, so it is refused while the declaring module
#: is imported rather than surfacing as a client-side 400 in production.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolSpecError(ValueError):
    """A spec breaks a registry invariant (programmer error, raised at import)."""


@lru_cache(maxsize=1)
def valid_tool_scopes() -> frozenset[str]:
    """The frozen action vocabulary a tool ``scope`` may name.

    Read from :data:`zolai.api.auth.VALID_ACTIONS` (33 actions, frozen — *not*
    editable from here) through a lazy import: the API routers import this
    package, so a module-level import would close a cycle.
    """
    from ...api.auth import VALID_ACTIONS  # lazy: no import-time cycle

    return VALID_ACTIONS


def validate_tool_name(name: str) -> str:
    """Return ``name`` when it is a legal tool name, else raise :class:`ToolSpecError`."""
    if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
        raise ToolSpecError(
            f"illegal tool name {name!r}: one spec serves both the OpenAI tools "
            f"array and MCP tools/list, so a name must match {TOOL_NAME_RE.pattern} "
            f"(letters, digits, '_' and '-' only — no dots)"
        )
    return name


def validate_tool_scope(scope: str | None) -> str | None:
    """Return ``scope`` when it is an exact action of the frozen vocabulary.

    ``None`` means "no narrower requirement than the caller's tier", which is
    legal.  Anything else must be an *exact* member of
    :data:`zolai.api.auth.VALID_ACTIONS`: no grant sugar (``catalog:*``), no
    empty string — a typo must fail at import, never silently narrow or
    silently widen a published surface.
    """
    if scope is None:
        return None
    if not isinstance(scope, str) or not scope.strip():
        raise ToolSpecError(
            f"illegal tool scope {scope!r}: expected a non-empty action from the "
            f"frozen {len(valid_tool_scopes())}-action vocabulary"
        )
    clean = scope.strip()
    if clean not in valid_tool_scopes():
        raise ToolSpecError(
            f"unknown tool scope {clean!r} — not in the frozen "
            f"{len(valid_tool_scopes())}-action vocabulary "
            f"(zolai.api.auth.VALID_ACTIONS)"
        )
    return clean


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: JSON-Schema shape + classification flags."""

    name: str
    description: str
    parameters: dict[str, Any]
    admin_only: bool = False
    requires_network: bool = False
    #: Writes something (additive proposal surfaces only — nothing here deletes
    #: or overwrites).  Drives the honest MCP read-only/idempotent hints.
    writes: bool = False
    #: Optional presentation title. Empty means "use the shared title map in
    #: :mod:`zolai.agent.tools.render`", so no existing spec's title changes;
    #: a generated spec family (the ``foundation_*`` tools) carries its own.
    title: str = ""
    #: Does the tool **destroy or mutate** durable state?  Rendered as the MCP
    #: ``destructiveHint``.
    #:
    #: ``None`` means *undeclared* and resolves to ``True`` in
    #: :attr:`is_destructive` — the MCP schema's own default for
    #: ``destructiveHint`` ("if false, the tool performs only additive
    #: updates").  The default therefore fails **closed**: a spec that forgets
    #: to declare is advertised as possibly destructive, so a client confirms
    #: before calling, instead of being told the tool is harmless.  Every
    #: *registered* spec declares it explicitly — enforced at import by
    #: :func:`zolai.agent.tools.registry.assert_specs_are_publishable`.
    destructive: bool | None = None
    #: Optional per-tool scope from the frozen action vocabulary.  It can only
    #: **narrow** what the caller's tier already allows: a tool whose scope the
    #: caller does not hold is absent from that caller's tool set, never an
    #: error at call time.  ``None`` = no narrowing beyond the tier.
    scope: str | None = None

    def __post_init__(self) -> None:
        # Both invariants run while the declaring module is imported, so a bad
        # catalogue entry is a start-up failure, not a production 400.
        validate_tool_name(self.name)
        validate_tool_scope(self.scope)

    @property
    def is_destructive(self) -> bool:
        """The MCP ``destructiveHint`` value for this spec (see :attr:`destructive`)."""
        if self.destructive is None:
            return True
        return bool(self.destructive)

    def json_schema(self) -> dict[str, Any]:
        """Shape embedded in the prompt-embedded ``## TOOLS`` section."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def native_def(self) -> dict[str, Any]:
        """OpenAI native ``tools`` entry — rendered from this one definition.

        Delegates to :func:`zolai.agent.tools.render.render_openai` so the
        OpenAI shape and the MCP shape can never drift; the output is
        byte-identical to the shape this method used to build by hand.
        """
        from .render import render_openai  # lazy: keeps ``types`` dependency-free

        return render_openai(self)


class ToolErrorCode(str, Enum):
    """Stable, client-safe failure classes for a tool result.

    A ``str`` enum so the value serialises to a plain string on every wire
    (MCP ``structuredContent``, the agent trace, an OpenAI-compatible body)
    without a custom encoder, and so a client can branch on it.
    """

    #: The caller's fault: a missing, ill-typed or out-of-range argument.
    INVALID_ARGUMENT = "invalid_argument"
    #: The request was well-formed but nothing matched.
    NOT_FOUND = "not_found"
    #: The caller may not use this tool (allow-list / scope refusal).
    FORBIDDEN = "forbidden"
    #: The server's fault: a dependency (a table, a model, an upstream) is down.
    UNAVAILABLE = "unavailable"
    #: The request exceeded its time budget.
    TIMEOUT = "timeout"
    #: Anything else. The detail stays in the server log, never on the wire.
    INTERNAL = "internal"


class ToolError(Exception):
    """Handler-level failure surfaced as an error result (never raised out).

    Lives here (not in :mod:`zolai.agent.tools.executor`) so a handler family
    that the executor merely *dispatches* — such as
    :mod:`zolai.agent.tools.foundation` — can raise it without importing the
    executor back.

    ``code`` classifies the failure.  It defaults to
    :attr:`ToolErrorCode.INVALID_ARGUMENT` because every pre-existing raise
    site validates the caller's arguments; a handler reporting a *server-side*
    problem must pass :attr:`ToolErrorCode.UNAVAILABLE` explicitly, so a
    dependency outage is never mislabelled as the caller's mistake.
    """

    def __init__(
        self, message: str, code: ToolErrorCode = ToolErrorCode.INVALID_ARGUMENT
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ToolCall:
    """A tool invocation parsed from a marker block or native ``tool_calls``."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    #: Raw ``function.arguments`` string from a native tool call (echoed back
    #: verbatim by the native multi-turn replay; empty for a marker call).
    arguments: str = ""
    turn: int = 0
    source: str = "marker"  # marker | native


@dataclass
class ToolResult:
    """Outcome of one tool execution — always ``{ok, data|error, latency_ms}``."""

    tool_name: str
    ok: bool
    latency_ms: float = 0.0
    data: Any = None
    error: str | None = None
    #: Typed classification of ``error`` — ``None`` exactly when ``ok`` is True.
    error_code: ToolErrorCode | None = None
    turn: int = 0
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.tool_name,
            "ok": self.ok,
            "latency_ms": round(self.latency_ms, 2),
            "turn": self.turn,
        }
        if self.ok:
            out["data"] = self.data
        else:
            out["error"] = self.error
            out["error_code"] = self.error_code.value if self.error_code else None
        return out

    def as_followup(self) -> dict[str, Any]:
        """``<<<TOOL_RESULT>>>`` payload fed back to the model."""
        if self.ok:
            return {"name": self.tool_name, "result": self.data}
        return {"name": self.tool_name, "error": self.error or "tool failed"}
