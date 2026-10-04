"""Tool primitives for the agent runtime (P3 §C).

Server-side only — the client never supplies a tool list; the registry is
the single source of truth and the executor enforces the caller's allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool: JSON-Schema shape + classification flags."""

    name: str
    description: str
    parameters: dict[str, Any]
    admin_only: bool = False
    requires_network: bool = False

    def json_schema(self) -> dict[str, Any]:
        """Shape embedded in the prompt-embedded ``## TOOLS`` section."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def native_def(self) -> dict[str, Any]:
        """OpenAI native ``tools`` entry (only ever sent to openai/openrouter)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    """A tool invocation parsed from a marker block or native ``tool_calls``."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
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
        return out

    def as_followup(self) -> dict[str, Any]:
        """``<<<TOOL_RESULT>>>`` payload fed back to the model."""
        if self.ok:
            return {"name": self.tool_name, "result": self.data}
        return {"name": self.tool_name, "error": self.error or "tool failed"}
