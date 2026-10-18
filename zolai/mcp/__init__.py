"""MCP layer for ``zolai-core`` — the tool surface a ChatGPT/Gemini/MCP client consumes.

The transport is :mod:`zolai.api.mcp_router` (``POST /api/v1/mcp``); this
package holds the protocol mechanics only (pure, stateless, no I/O).
"""

from __future__ import annotations

from . import protocol

__all__ = ["protocol"]
