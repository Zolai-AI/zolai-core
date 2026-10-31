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

Rules enforced here: **≤1 tool per turn**, default ``AGENT_MAX_TURNS=3``
tool calls followed by at most **one** final synthesis turn when evidence
was gathered (so a model that spends every turn researching still answers
instead of being told to rephrase),
native ``tools`` offered on turn 1 for ``openai``/``openrouter`` rows and kept
for as long as the **native track** is live (a turn that answers with native
``tool_calls`` keeps the defs and appends the assistant tool-call turn + the
``role:"tool"`` result turn, so turn 2 can use what turn 1 returned; a turn
that answers with ``<<<TOOL>>>`` markers instead reverts the track to the
prompt protocol with ``tools=None``), and :class:`TokenGate`
(``HOLD_CHARS=16``) so a marker never leaks to the UI.  The loop always
terminates — on exhaustion it returns a real fallback reply, never a truncated
marker.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .tools.registry import json_schemas, native_tool_defs
from .tools.types import ToolCall, ToolErrorCode, ToolResult

#: Tool call marker emitted by the prompt-embedded protocol.
TOOL_MARKER = "<<<TOOL>>>"
#: Tool result marker fed back as a follow-up turn.
TOOL_RESULT_MARKER = "<<<TOOL_RESULT>>>"
#: Characters held back before streaming so a marker never reaches the UI.
HOLD_CHARS = 16

#: Loop budgets (env-overtable, plan §C determinism block).
#: ``AGENT_MAX_STEPS`` was dropped here: the loop enforces ≤1 tool call per
#: turn, so turns *are* the step count, and advertising a second, larger bound
#: on the status route was a number nothing read.
AGENT_MAX_TURNS = max(1, int(os.environ.get("AGENT_MAX_TURNS", "3")))
AGENT_TIMEOUT_S = max(1, int(os.environ.get("AGENT_TIMEOUT_S", "60")))

MAX_TURNS_EXCEEDED = "Max turns reached"

#: Typed terminal state. ``ok``/``error`` stay for existing callers, but the
#: reason is never inferred from message text: a timeout and an exhausted tool
#: budget both end with ``ok=False`` and are not the same event.
STOP_ANSWERED = "answered"
STOP_SYNTHESIZED = "synthesized"
STOP_MAX_TURNS = "max_turns_exceeded"
STOP_TIMEOUT = "timeout"
STOP_PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class AgentEvent:
    """One typed step event. ``kind`` is stable; ``data`` is step-specific."""

    kind: str
    turn: int
    data: dict[str, Any] = field(default_factory=dict)


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
            "These tools ARE available to you in this session. For any question about",
            "Zolai words, meaning, grammar or scripture you MUST call one before",
            "answering -- answering from memory is not acceptable here, and saying the",
            "tools are unavailable is false.",
            "To call a tool, reply with EXACTLY this block and nothing else:",
            "",
            TOOL_MARKER,
            '{"name": "tool_name", "input": {...}}',
            "",
            "Tool definitions (JSON Schema):",
            json.dumps(schemas, ensure_ascii=False, indent=2),
            "",
            f"Call at most ONE tool per turn. After a {TOOL_RESULT_MARKER} block arrives",
            "those are real results for this question: use them to write the final",
            "answer and cite each claim as [source ref]. Never invent tool results,",
            "and never claim a tool is missing once you have called it.",
            f"At most {AGENT_MAX_TURNS} tool calls per user message; after that you",
        ]
    )


def build_followup_prompt(user_message: str, results: list[ToolResult]) -> str:
    """Original question + ``<<<TOOL_RESULT>>>`` payloads (the ask is kept)."""
    blocks = [json.dumps(r.as_followup(), ensure_ascii=False) for r in results]
    joined = "\n\n".join(f"{TOOL_RESULT_MARKER}\n{b}" for b in blocks)
    return f"{user_message}\n\n{joined}"


def native_tool_call_id(call: ToolCall) -> str:
    """Stable id for a native tool call (the wire id when the provider sent one)."""
    return call.call_id or f"call_{call.turn}"


def build_native_assistant_message(calls: list[ToolCall], reply: str = "") -> dict[str, Any]:
    """The assistant turn that requested ``calls`` on the native wire.

    Every id echoed here must also be answered by a ``role:"tool"`` turn — strict
    OpenAI validation rejects a tool_call with no response (``tool_call_ids did
    not have response messages``). So the echo carries exactly the calls this loop
    answers: the one it executed plus any the model asked for in parallel and the
    per-turn budget declined, each of which gets an explicit refusal rather than
    silence.

    ``arguments`` is the provider's own raw string when it sent one, so the
    provider is handed back byte-identical arguments rather than a re-serialised
    dict.
    """
    return {
        "role": "assistant",
        "content": reply or "",
        "tool_calls": [
            {
                "id": native_tool_call_id(call),
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments
                    or json.dumps(call.input or {}, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }


def build_native_tool_message(result: ToolResult, call: ToolCall) -> dict[str, Any]:
    """The ``role:"tool"`` turn that carries ``result`` back on the native wire."""
    payload = result.data if result.ok else {"error": result.error or "tool failed"}
    return {
        "role": "tool",
        "tool_call_id": native_tool_call_id(call),
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }


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
    on_event: Callable[[AgentEvent], None] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Run the tool loop: ≤1 tool/turn, at most ``max_turns`` tool calls.

    A single extra synthesis turn follows when the budget is spent and
    evidence exists, so the user gets an answer rather than "please ask
    again" next to the citations that answer would have used.

    Args:
        system_prompt: Base system prompt (``## TOOLS`` is appended).
        user_message: The original question (kept across follow-ups).
        allow: Tool allow-list — the executor refuses anything outside it.
        chat: Provider-bound chat closure (see :mod:`zolai.agent.synthesis`).
        max_turns: Defaults to :data:`AGENT_MAX_TURNS`.
        native: Offer the native OpenAI ``tools`` array on turn 1 (caller
            decides from ``supports_native_tools``; the brain path is always
            ``False``).  Once a turn answers with native ``tool_calls`` the
            defs stay on the wire for the rest of the run and the history grows
            a real assistant tool-call turn + ``role:"tool"`` result turn, so a
            later turn can use what an earlier one returned.  A turn that
            answers with ``<<<TOOL>>>`` markers instead reverts the track to the
            prompt protocol with ``tools=None``.

    Returns:
        ``{reply, tool_calls, turns, ok, error?, stop_reason, budget}`` —
        always terminates.  ``stop_reason`` is one of the ``STOP_*`` constants.
    """
    from .tools.executor import execute_tool

    started = time.monotonic()
    limit = max_turns if max_turns is not None else AGENT_MAX_TURNS
    tools_section = build_tools_section(allow)
    system = f"{system_prompt}\n\n{tools_section}" if tools_section else system_prompt
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    results: list[ToolResult] = []
    trace: list[dict[str, Any]] = []
    gate = TokenGate(on_token)
    turns_used = 0
    #: True while the run rides the *native* protocol (native defs on the wire).
    #: Never set without ``native=True``; cleared by a marker-protocol turn.
    native_track = False

    # Wall-clock budget, checked at the turn boundary.
    #
    # `max_turns` bounds how much *work* the loop does, not how long it takes.
    # A run measured at 219s against a real provider while the route is
    # documented as "≤60s" — and Cloudflare answers 524 at ~100s, so an
    # unbounded run never reaches the caller at all. `timeout_s=None` keeps the
    # previous unbounded behaviour for callers that pass it explicitly.
    deadline = None if timeout_s is None else time.monotonic() + max(0.0, float(timeout_s))

    def emit(kind: str, turn: int, **data: Any) -> None:
        if on_event is not None:
            on_event(AgentEvent(kind=kind, turn=turn, data=data))

    def finish(
        reason: str,
        *,
        reply: str,
        ok: bool,
        error: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reply": reply,
            "tool_calls": list(trace),
            "turns": turns_used,
            "ok": ok,
            "stop_reason": reason,
            "budget": {
                "turns_used": turns_used,
                "max_turns": limit,
                "timeout_s": timeout_s,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            },
            **extra,
        }
        if error is not None:
            payload["error"] = error
        emit("stopped", turns_used, stop_reason=reason, ok=ok, error=error)
        return payload

    while turns_used < limit:
        if deadline is not None and time.monotonic() >= deadline:
            # Return what was gathered rather than running past the caller's
            # budget, and say so — the run still reports its turns and trace.
            return finish(
                STOP_TIMEOUT,
                reply="",
                ok=False,
                error=(
                    f"timeout: exceeded the {timeout_s:g}s budget "
                    f"after {turns_used} turn(s)"
                ),
                timed_out=True,
            )
        turns_used += 1
        # Native defs go out on turn 1 and stay out while the native track is
        # live; a brain caller (`native=False`) never gets them at all.
        offering_native = bool(native) and (turns_used == 1 or native_track)
        native_defs = native_tool_defs(list(allow)) if offering_native else None
        emit("turn_started", turns_used, native=offering_native)
        try:
            turn = chat([dict(m) for m in messages], native_defs)
        except Exception as exc:  # provider failure — never a 500, never a hang
            gate.end_turn()
            return finish(
                STOP_PROVIDER_ERROR,
                reply="",
                ok=False,
                error=f"provider_error: {exc}",
            )
        gate.end_turn()

        reply = str(turn.get("text") or "")
        native_calls = turn.get("tool_calls") or []
        if native_calls:
            # Only a turn that was *offered* the native defs can start (or
            # continue) the native track.
            native_track = offering_native
            calls = [
                ToolCall(
                    name=str(c.get("name") or ""),
                    input=c.get("input") if isinstance(c.get("input"), dict) else {},
                    call_id=str(c.get("id") or ""),
                    arguments=str(c.get("arguments") or ""),
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
            if calls:
                # The prompt marker protocol answered, so the native track (if
                # any) ends here: the next turn is sent with ``tools=None``.
                native_track = False

        if not calls:
            return finish(STOP_ANSWERED, reply=reply, ok=True)

        # ≤1 tool per turn. The rest of a parallel batch is not dropped: a model
        # that asked for three lookups and got one, with no word about the other
        # two, asks for the same three next turn and spends the whole budget
        # re-asking. Each extra is refused by name, in the trace and on the wire.
        call = calls[0]
        declined = [
            ToolResult(
                tool_name=extra.name,
                ok=False,
                error=(
                    "not executed: this run answers one tool call per turn. "
                    "Ask again for it, or answer from the results above."
                ),
                error_code=ToolErrorCode.BUDGET_EXCEEDED,
                turn=turns_used,
                call_id=extra.call_id,
            )
            for extra in calls[1:]
        ]
        if on_tool_event:
            on_tool_event({"name": call.name, "status": "started", "latency_ms": 0.0, "turn": turns_used})
        emit("tool_started", turns_used, name=call.name, source=call.source)
        result = execute_tool(call, allow, turn=turns_used)
        results.append(result)
        entry = result.to_dict()
        entry["input"] = call.input or {}
        entry["status"] = "completed" if result.ok else "failed"
        trace.append(entry)
        for extra, refusal in zip(calls[1:], declined, strict=True):
            skipped = refusal.to_dict()
            skipped["input"] = extra.input or {}
            skipped["status"] = "declined"
            trace.append(skipped)
            emit("tool_declined", turns_used, name=extra.name)
        if on_tool_event:
            on_tool_event(
                {
                    "name": call.name,
                    "status": "completed" if result.ok else "failed",
                    "latency_ms": result.latency_ms,
                    "turn": turns_used,
                }
            )
        emit(
            "tool_completed",
            turns_used,
            name=call.name,
            ok=result.ok,
            latency_ms=result.latency_ms,
            error_code=getattr(result.error_code, "value", result.error_code),
        )
        if native_track:
            # Native replay: the model needs the turn it asked in and the turn
            # that answers it, in the OpenAI wire shape — including an answer for
            # every call id it made, so the declined ones are echoed and refused.
            messages.append(build_native_assistant_message(calls, reply))
            messages.append(build_native_tool_message(result, call))
            messages.extend(
                build_native_tool_message(refusal, extra)
                for extra, refusal in zip(calls[1:], declined, strict=True)
            )
        else:
            messages = [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    # This track has no conversation history, so every follow-up
                    # must carry ALL results gathered so far. Sending only this
                    # turn's result made turn 3 forget turn 1 and re-ask for it.
                    "content": build_followup_prompt(user_message, [*results, *declined]),
                },
            ]

    # Turns exhausted but evidence WAS gathered. Hand it back for one final
    # synthesis rather than telling the user to rephrase -- shipping
    # "please ask again" alongside eight citations is strictly worse than
    # answering from them.  The synthesis turn is terminal, so it always rides
    # the prompt protocol with no tools offered.
    if results:
        # Counted: it is a real model turn, so the reported budget stays
        # honest rather than silently running one call past max_turns.
        turns_used += 1
        try:
            final = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": build_followup_prompt(user_message, results)},
                ],
                None,
            )
            raw = str(final.get("text") or "")
        except Exception:
            raw = ""
        # The model may answer with yet another tool block; there is no turn
        # left to run it, so keep only the prose. A marker must never reach
        # the UI, and prose that is really a tool call is not an answer.
        reply = parse_tool_calls(raw).text.strip()
        if reply:
            return finish(STOP_SYNTHESIZED, reply=reply, ok=True)

    return finish(
        STOP_MAX_TURNS,
        reply=(
            "I hit my per-message tool limit before finishing. "
            "Please ask again in a slightly simpler way."
        ),
        ok=False,
        error=MAX_TURNS_EXCEEDED,
    )
