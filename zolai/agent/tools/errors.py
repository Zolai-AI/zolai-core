"""Typed, client-safe failure classification for the agent layer.

A failure here is *shown to a person*: over MCP it is the text a chat client
renders, on the agent page it is the run's ``error``, and inside the loop it is
what the model reads back in a ``<<<TOOL_RESULT>>>`` block.  A raw exception
string is wrong for all three — it is not human-readable, it names internal
types and modules, and it can echo SQL, file paths or provider payloads into
something that is published to a user.

So every failure is mapped to a :class:`ToolErrorCode` plus a short,
human-readable sentence.  The raw exception goes to the server log (and to the
operator-facing admin notification) and nowhere else.  Two causes are never
conflated:

- **the caller's fault** (a missing or ill-typed argument) — the message names
  the field and what was wrong with it, because that is actionable;
- **the server's fault** (an unavailable dependency, an internal error) — the
  message says so plainly and carries no internals, because the caller can do
  nothing with a traceback.

The sentences are deliberately **subject-neutral**: the identity of the failing
thing travels structurally (``ToolResult.tool_name``, the run id, MCP's
``structuredContent.error.tool``), so the same vocabulary serves a tool call, an
agent run and anything else that has to explain a failure to a person.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .types import ToolError, ToolErrorCode

#: Human-readable sentence per code.  Written for the end user, not for a log:
#: no exception name, no module path, no argument dump.
DEFAULT_MESSAGES: Mapping[ToolErrorCode, str] = {
    ToolErrorCode.INVALID_ARGUMENT: "The arguments were not valid for this request.",
    ToolErrorCode.NOT_FOUND: "Nothing matched that request.",
    ToolErrorCode.FORBIDDEN: "The caller is not allowed to use that.",
    ToolErrorCode.UNAVAILABLE: (
        "A dependency this needs is temporarily unavailable. Please try again shortly."
    ),
    ToolErrorCode.TIMEOUT: "The request took too long and timed out. Please try again.",
    ToolErrorCode.INTERNAL: "The request could not be completed. Please try again.",
}

#: Signals that a string is a raw Python exception or traceback rather than a
#: sentence written for a person: an exception-class prefix (``TypeError: ...``),
#: a traceback header, or a source-frame line.
_RAW_EXCEPTION_RE: Final = re.compile(
    r"Traceback \(most recent call last\)"
    r"|\b\w+(?:Error|Exception)\b\s*:"
    r"|\bFile \"[^\"]+\", line \d+"
)


@dataclass(frozen=True)
class ToolFailure:
    """A classified failure: a stable code plus a human-readable message."""

    code: ToolErrorCode
    message: str


def message_for(code: ToolErrorCode) -> str:
    """The default human-readable sentence for ``code``."""
    return DEFAULT_MESSAGES[code]


def safe_message(code: ToolErrorCode, message: str | None) -> str:
    """The one message a caller may show a person — the publishing boundary.

    ``ToolResult.error`` is *supposed* to be human-readable already (the
    executor classifies every exception through :func:`classify_failure`), but a
    boundary that trusts its input is not a boundary: a result can be built by
    hand, and a handler can interpolate an exception into its own message.  So
    the raw-detail guard is applied here, once, at the point where a string
    becomes visible to a user, and the canonical sentence for ``code`` is
    substituted when it trips.  This is defence in depth, not the only defence.
    """
    if message and not _RAW_EXCEPTION_RE.search(message):
        return message
    return message_for(code)


def classify_failure(exc: BaseException) -> ToolFailure:
    """Map an unexpected exception to a code plus a client-safe message.

    ``ToolError`` is authored by a handler and already carries a deliberate
    message and a code, so it passes through.  Everything else is classified
    only where the cause is unambiguous — a wrong guess would mislabel a
    server-side outage as the caller's mistake — and an unrecognised exception
    is ``INTERNAL`` with its detail left in the log.  Note that
    ``TimeoutError`` is a subclass of ``OSError``, so it must be tested first.
    """
    if isinstance(exc, ToolError):
        return ToolFailure(exc.code, str(exc) or message_for(exc.code))
    if isinstance(exc, TimeoutError):
        return ToolFailure(ToolErrorCode.TIMEOUT, message_for(ToolErrorCode.TIMEOUT))
    if isinstance(exc, ModuleNotFoundError):
        return ToolFailure(
            ToolErrorCode.UNAVAILABLE,
            "A component this needs is not installed on the server.",
        )
    if isinstance(exc, OSError):
        return ToolFailure(ToolErrorCode.UNAVAILABLE, message_for(ToolErrorCode.UNAVAILABLE))
    return ToolFailure(ToolErrorCode.INTERNAL, message_for(ToolErrorCode.INTERNAL))


__all__ = [
    "DEFAULT_MESSAGES",
    "ToolFailure",
    "classify_failure",
    "message_for",
    "safe_message",
]
