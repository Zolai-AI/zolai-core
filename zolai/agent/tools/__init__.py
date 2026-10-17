"""Tool sub-package: registry (schemas) + allow-listed executor."""

from __future__ import annotations

from .executor import execute_tool
from .registry import (
    allow_list,
    get_tool,
    json_schemas,
    mcp_tools,
    names_for,
    native_tool_defs,
    openai_tool_defs,
)
from .render import render_many, render_mcp, render_openai
from .types import ToolCall, ToolResult, ToolSpec

__all__ = [
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "allow_list",
    "execute_tool",
    "get_tool",
    "json_schemas",
    "mcp_tools",
    "names_for",
    "native_tool_defs",
    "openai_tool_defs",
    "render_many",
    "render_mcp",
    "render_openai",
]
