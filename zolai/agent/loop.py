"""Marker-protocol agent loop (P3 §C) — Python port of pcore-assistant's
``services/agent-loop.ts``.

Protocol reality: the **brain path never receives a native ``tools`` key**
(see :func:`zolai.llm.adapter.build_chat_body`), so the primary channel is
prompt-embedded JSON:

- a ``## TOOLS`` JSON-Schema section appended to the system prompt;
- tool calls arrive as ``<<<TOOL>>>\\n{"name": ..., "input": {...}}`` blocks,
  parsed with a **brace scan** (fence-tolerant, nested objects safe);
- results ride back as ``<<<TOOL_RESULT>>>`` follow-up turns that keep the
  original question in the user message.

Rules enforced here: **≤1 tool per turn**, default ``AGENT_MAX_TURNS=3``,
native ``tools`` offered on turn 1 **only** for ``openai``/``openrouter``
rows, and :class:`TokenGate` (``HOLD_CHARS=16``) so a marker never leaks to
the UI.  The loop always terminates — on exhaustion it returns a real
fallback reply, never a truncated marker.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .tools.registry import json_schemas, native_tool_defs
from .tools.types import ToolCall, ToolResult

#: Tool call marker emitted by the prompt-embedded protocol.
TOOL_MARKER = "<<<TOOL>>>"
#: Tool result marker fed back as a follow-up turn.
TOOL_RESULT_MARKER = "<<<TOOL_RESULT>>>"
#: Characters held back before streaming so a marker never reaches the UI.
HOLD_CHARS = 16

#: Loop budgets (env-overtable, plan §C determinism block).
AGENT_MAX_TURNS = max(1, int(os.environ.get("AGENT_MAX_TURNS", "3")))
AGENT_MAX_STEPS = max(1, int(os.environ.get("AGENT_MAX_STEPS", "16")))
AGENT_TIMEOUT_S = max(1, int(os.environ.get("AGENT_TIMEOUT_S", "60")))

MAX_TURNS_EXCEEDED = "Max turns reached"

#: ``chat(messages, tools) -> {"text", "tool_calls", "latency_ms"?}``
ChatFn = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], dict[str, Any]]


@dataclass
class ParsedToolCalls:
    """Result of scanning a model turn for ``<<<TOOL>>>`` blocks."""

    text: str
    calls: list[ToolCall] = field(default_factory=list)


def find_payload_start(text: str, start: int) -> int:
    """Index of the opening ``{`` after a marker (skips whitespace + fences).

    Also tolerates a fenced language tag (e.g. `````json``) between the
    marker and the payload, matching the reference agent protocol. The tag is
    only skipped when it directly follows fence/whitespace characters, so
    plain prose after a stray marker is never scanned for a later ``{``.
    """
    i = start
    n = len(text)
    saw_fence = False
    while i < n:
        ch = text[i]
        if ch in " \t\n\r`~":
            saw_fence = saw_fence or ch in "`~"
            i += 1
            continue
        if saw_fence and ch.isalpha():
            j = i
            while j < n and (text[j].isalnum() or text[j] in "+-"):
                j += 1
            if j < n and text[j] in " \t\n\r":
                i = j
                saw_fence = False  # consume the tag, then whitespace only
                continue
            return -1
        return i if ch == "{" else -1
    return -1


def find_json_end(text: str, start: int) -> int:
    """Index just past the ``}`` matching the ``{`` at ``start`` (strings aware)."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _skip_closing_fence(text: str, pos: int) -> int:
    """Advance past a code fence that directly follows a payload (`````json`` blocks)."""
    j = pos
    while j < len(text) and text[j] in " \t\n\r":
        j += 1
    if text.startswith("```", j) or text.startswith("~~~", j):
        k = j
        while k < len(text) and text[k] in " \t\n\r`~":
            k += 1
        return k
    return pos


def parse_tool_calls(text: str) -> ParsedToolCalls:
    """Extract ``<<<TOOL>>>`` calls from a model turn (brace scan, fence-tolerant).

    A marker with a parseable JSON object becomes a call and is stripped from
    the reply; a marker with no/corrupt payload is stripped too (a raw marker
    must never leak to the user).  Everything else survives verbatim.
    """
    calls: list[ToolCall] = []
    out: list[str] = []
    cursor = 0

    while True:
        marker_at = text.find(TOOL_MARKER, cursor)
        if marker_at == -1:
            out.append(text[cursor:])
            break
        out.append(text[cursor:marker_at])

        payload_start = find_payload_start(text, marker_at + len(TOOL_MARKER))
        if payload_start == -1:
            cursor = marker_at + len(TOOL_MARKER)
            continue
        payload_end = find_json_end(text, payload_start)
        if payload_end == -1:
            cursor = marker_at + len(TOOL_MARKER)
            continue
        try:
            parsed = json.loads(text[payload_start:payload_end])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and str(parsed.get("name") or "").strip():
            calls.append(
                ToolCall(
                    name=str(parsed["name"]),
                    input=parsed.get("input") if isinstance(parsed.get("input"), dict) else {},
                )
            )
        cursor = _skip_closing_fence(text, payload_end)

    return ParsedToolCalls(text="".join(out).strip(), calls=calls)


def build_tools_section(names: Iterable[str]) -> str:
    """The ``## TOOLS`` system-prompt section (JSON-Schema embedded)."""
    schemas = json_schemas(list(names))
    if not schemas:
        return ""
    return "\n".join(
        [
            "## TOOLS",
            "You have access to the following tools. Only call a tool when the answer",
            "actually needs live data — otherwise answer directly.",
            "To call a tool, reply with EXACTLY this block and nothing else:",
            "",
            TOOL_MARKER,
            '{"name": "tool_name", "input": {...}}',
            "",
            "Tool definitions (JSON Schema):",
            json.dumps(schemas, ensure_ascii=False, indent=2),
            "",
            f"Call at most ONE tool per turn. After a {TOOL_RESULT_MARKER} block arrives",
            "use it to write the final answer. Never invent tool results.",
            f"Maximum {AGENT_MAX_TURNS} turns per user message.",
        ]
    )


def build_followup_prompt(user_message: str, results: list[ToolResult]) -> str:
    """Original question + ``<<<TOOL_RESULT>>>`` payloads (the ask is kept)."""
    blocks = [json.dumps(r.as_followup(), ensure_ascii=False) for r in results]
    joined = "\n\n".join(f"{TOOL_RESULT_MARKER}\n{b}" for b in blocks)
    return f"{user_message}\n\n{joined}"


def _marker_prefix_suffix(text: str) -> str:
    """Longest non-empty suffix of ``text`` that is a (proper) prefix of the marker."""
    for k in range(len(TOOL_MARKER) - 1, 0, -1):
        if text.endswith(TOOL_MARKER[:k]):
            return TOOL_MARKER[:k]
    return ""


class TokenGate:
    """Holds back ``HOLD_CHARS`` so a ``<<<TOOL>>>`` never reaches the UI.

    Streaming sink is optional; the gate is also exercised by unit tests
    without one (flush/drop accounting).
    """

    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink
        self._turn_text = ""
        self._pending = ""
        self._decided = False
        self._suppressed = False

    def push(self, token: str) -> None:
        if not token:
            return
        self._turn_text += token
        if TOOL_MARKER in self._turn_text:
            self._suppressed = True
            self._pending = ""
            return
        if self._sink is None:
            return
        self._pending += token
        if not self._decided:
            if len(self._pending) < HOLD_CHARS:
                return
            self._decided = True
        keep = len(TOOL_MARKER) - 1
        if len(self._pending) > keep:
            out = self._pending[: len(self._pending) - keep]
            self._pending = self._pending[len(self._pending) - keep :]
            self._sink(out)

    def end_turn(self) -> str:
        """End of turn — flush the tail, but never leak a marker prefix."""
        dropped = self._suppressed
        if self._pending and self._sink is not None:
            tail = _marker_prefix_suffix(self._pending)
            if tail:
                dropped = True
                clean = self._pending[: len(self._pending) - len(tail)]
                if clean:
                    self._sink(clean)
            else:
                self._sink(self._pending)
        self._turn_text = ""
        self._pending = ""
        self._decided = False
        self._suppressed = False
        return "dropped" if dropped else "flushed"


def run_agent_loop(
    *,
    system_prompt: str,
    user_message: str,
    allow: frozenset[str] | set[str] | tuple[str, ...],
    chat: ChatFn,
    max_turns: int | None = None,
    native: bool = False,
    on_token: Callable[[str], None] | None = None,
    on_tool_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the tool loop: ≤1 tool/turn, at most ``max_turns`` model turns.

    Args:
        system_prompt: Base system prompt (``## TOOLS`` is appended).
        user_message: The original question (kept across follow-ups).
        allow: Tool allow-list — the executor refuses anything outside it.
        chat: Provider-bound chat closure (see :mod:`zolai.agent.synthesis`).
        max_turns: Defaults to :data:`AGENT_MAX_TURNS`.
        native: Offer the native OpenAI ``tools`` array on **turn 1 only**
            (caller decides from ``supports_native_tools``; the brain path is
            always ``False``).

    Returns:
        ``{reply, tool_calls, turns, ok, error?}`` — always terminates.
    """
    from .tools.executor import execute_tool

    limit = max_turns if max_turns is not None else AGENT_MAX_TURNS
    tools_section = build_tools_section(allow)
    system = f"{system_prompt}\n\n{tools_section}" if tools_section else system_prompt
    message = user_message
    results: list[ToolResult] = []
    trace: list[dict[str, Any]] = []
    gate = TokenGate(on_token)
    turns_used = 0

    while turns_used < limit:
        turns_used += 1
        native_defs = native_tool_defs(list(allow)) if (native and turns_used == 1) else None
        try:
            turn = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                native_defs,
            )
        except Exception as exc:  # provider failure — never a 500, never a hang
            gate.end_turn()
            return {
                "reply": "",
                "tool_calls": list(trace),
                "turns": turns_used,
                "ok": False,
                "error": f"provider_error: {exc}",
            }
        gate.end_turn()

        reply = str(turn.get("text") or "")
        native_calls = turn.get("tool_calls") or []
        if native_calls:
            calls = [
                ToolCall(
                    name=str(c.get("name") or ""),
                    input=c.get("input") if isinstance(c.get("input"), dict) else {},
                    turn=turns_used,
                    source="native",
                )
                for c in native_calls
                if isinstance(c, dict)
            ]
        else:
            parsed = parse_tool_calls(reply)
            reply = parsed.text
            calls = parsed.calls
            for c in calls:
                c.turn = turns_used

        if not calls:
            return {
                "reply": reply,
                "tool_calls": list(trace),
                "turns": turns_used,
                "ok": True,
            }

        # ≤1 tool per turn — extra markers in the same turn are ignored.
        call = calls[0]
        if on_tool_event:
            on_tool_event({"name": call.name, "status": "started", "latency_ms": 0.0, "turn": turns_used})
        result = execute_tool(call, allow, turn=turns_used)
        results.append(result)
        entry = result.to_dict()
        entry["input"] = call.input or {}
        entry["status"] = "completed" if result.ok else "failed"
        trace.append(entry)
        if on_tool_event:
            on_tool_event(
                {
                    "name": call.name,
                    "status": "completed" if result.ok else "failed",
                    "latency_ms": result.latency_ms,
                    "turn": turns_used,
                }
            )
        message = build_followup_prompt(user_message, [result])

    return {
        "reply": (
            "I hit my per-message tool limit before finishing. "
            "Please ask again in a slightly simpler way."
        ),
        "tool_calls": list(trace),
        "turns": turns_used,
        "ok": False,
        "error": MAX_TURNS_EXCEEDED,
    }
