"""Static landing page for the MCP tool surface — generated, never transcribed.

The page is a **rendering of the registry**, not a copy of it: every row comes
from :func:`zolai.agent.tools.registry.allow_list` (which tool belongs to which
caller) plus the :class:`~zolai.agent.tools.types.ToolSpec` that the MCP
``tools/list`` and the OpenAI ``tools`` array are rendered from, and the version
block comes from :mod:`zolai.mcp.protocol`.  A tool therefore cannot be
documented here without existing, and cannot be renamed in code without the page
changing on the next build — ``tests/test_mcp_landing.py`` fails if the two ever
disagree.

Access tiers are *derived* too: a tool is public when
:func:`~zolai.agent.tools.registry.allow_list` puts it in the anonymous set,
member when only a key reaches it, admin otherwise.  Nothing here grants
anything — the tiers describe what the server already enforces.

Pure and stdlib-only (``html.escape`` + ``json``), so it can be emitted from the
CLI (``zolai mcp landing --out …``) or from a build step with no dependencies.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..agent.tools.registry import SPECS, allow_list, names_for
from . import protocol

#: Tier order for display, least to most privileged.
TIERS: tuple[str, ...] = ("public", "member", "admin")

#: One honest sentence per tier, describing what the server already enforces.
TIER_BLURB: dict[str, str] = {
    "public": "No credential. Anonymous callers are rate limited per IP (10/min).",
    "member": "Any valid API key (X-API-Key or Authorization: Bearer).",
    "admin": "An admin key — one carrying apikey:manage / settings:write / user:manage / role:manage / *.",
}

#: The public endpoint a client points at (both are the same handler).
ENDPOINTS: tuple[str, ...] = (
    "https://api.zolai.space/mcp",
    "https://api.zolai.space/api/v1/mcp",
)


def tier_for(name: str) -> str:
    """Access tier a tool is actually reachable at, derived from the allow-lists.

    ``public`` → in the anonymous set; ``member`` → in the member set;
    ``admin`` → member plus admin extras / the optional web tool.
    """
    if name in allow_list("public"):
        return "public"
    if name in allow_list("member"):
        return "member"
    if name in allow_list("admin"):
        return "admin"
    return "unreachable"


def tool_rows() -> list[dict[str, Any]]:
    """One honest row per registered tool, in registry declaration order.

    Iterates :data:`~zolai.agent.tools.registry.SPECS` itself (not a scope),
    so a tool that is registered but sits in no allow-list still appears — as
    an explicit "not reachable" note rather than a silent omission.
    """
    rows: list[dict[str, Any]] = []
    for name in SPECS:
        spec = SPECS[name]
        rows.append(
            {
                "name": spec.name,
                "title": spec.title or name.replace("_", " "),
                "description": spec.description,
                "tier": tier_for(name),
                "admin_only": bool(spec.admin_only),
                "writes": bool(spec.writes),
                "requires_network": bool(spec.requires_network),
                "properties": sorted((spec.parameters.get("properties") or {}).keys()),
                "required": list(spec.parameters.get("required") or []),
            }
        )
    return rows


def _notes(row: dict[str, Any]) -> str:
    """The honest flags for a tool, in plain words (never an annotation guess)."""
    notes: list[str] = []
    if row["writes"]:
        notes.append("writes (additive proposal only)")
    else:
        notes.append("read-only")
    if row["requires_network"]:
        notes.append("needs network")
    if row["admin_only"]:
        notes.append("admin surface")
    return ", ".join(notes)


def _arguments(row: dict[str, Any]) -> str:
    """``query*``-style argument summary (``*`` marks a required argument)."""
    if not row["properties"]:
        return "—"
    required = set(row["required"])
    return ", ".join(
        f"{prop}*" if prop in required else prop for prop in row["properties"]
    )


def _tool_count_line() -> str:
    counts = ", ".join(f"{len(allow_list(tier))} {tier}" for tier in TIERS)
    return f"{len(SPECS)} registered tools — {counts}"


_STYLE = """
:root { color-scheme: light dark; }
body { font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0 auto; max-width: 60rem; padding: 2rem 1.25rem 4rem; }
h1 { font-size: 1.7rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2.25rem 0 .5rem; }
p, li { max-width: 60rem; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .92em; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0 0; }
th, td { border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
         padding: .45rem .6rem; text-align: left; vertical-align: top; }
th { font-weight: 600; }
td.name { white-space: nowrap; }
.tier { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }
.muted { opacity: .75; }
ul.endpoints { padding-left: 1.1rem; }
"""


def render_landing(*, generated_at: str | None = None) -> str:
    """The complete standalone HTML page (no external assets, no script).

    Args:
        generated_at: optional ISO timestamp to record in the page. Omitted
            from the output when ``None`` so two builds of the same registry
            produce byte-identical HTML (which is what the drift test compares).
    """
    rows = tool_rows()
    esc = html.escape

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(protocol.SERVER_NAME)} — MCP tools</title>",
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        "<h1>Zolai language toolkit — MCP tool surface</h1>",
        f'<p class="muted">{esc(_tool_count_line())}. '
        f"Protocol versions: <code>{esc(', '.join(protocol.SUPPORTED_PROTOCOL_VERSIONS))}</code> "
        f"(current: <code>{esc(protocol.PROTOCOL_VERSION)}</code>). "
        "Served by <code>"
        f"{esc(protocol.SERVER_NAME)} {esc(protocol.SERVER_VERSION)}</code>.</p>",
        "<h2>Connect</h2>",
        "<p>Point an MCP client at the streamable-HTTP endpoint:</p>",
        '<ul class="endpoints">',
    ]
    for endpoint in ENDPOINTS:
        parts.append(f"<li><code>{esc(endpoint)}</code> (POST)</li>")
    parts += [
        "</ul>",
        "<p>Modern clients may call <code>server/discover</code> first; "
        "handshake-era clients still open with <code>initialize</code>, and both "
        "are answered by the same endpoint. A request that declares an "
        "unimplemented protocol version is refused with <code>-32022</code> and the "
        "list of versions the server does implement.</p>",
        f'<p class="muted">{esc(protocol.SERVER_INSTRUCTIONS)}</p>',
    ]

    for tier in TIERS:
        tier_rows = [row for row in rows if row["tier"] == tier]
        if not tier_rows:
            continue
        parts.append(f'<h2 id="tier-{tier}">{esc(tier.title())} tools ({len(tier_rows)})</h2>')
        parts.append(f'<p class="muted">{esc(TIER_BLURB[tier])}</p>')
        parts.append("<table>")
        parts.append(
            "<thead><tr><th>Tool</th><th>What it does</th>"
            "<th>Arguments</th><th>Notes</th></tr></thead><tbody>"
        )
        for row in tier_rows:
            parts.append(
                f'<tr data-tool="{esc(row["name"])}" data-tier="{esc(row["tier"])}">'
                f'<td class="name"><code>{esc(row["name"])}</code><br>'
                f'<span class="tier muted">{esc(row["title"])}</span></td>'
                f"<td>{esc(row['description'])}</td>"
                f"<td><code>{esc(_arguments(row))}</code></td>"
                f'<td class="muted">{esc(_notes(row))}</td>'
                "</tr>"
            )
        parts.append("</tbody></table>")

    unreachable = [row for row in rows if row["tier"] == "unreachable"]
    if unreachable:
        names = ", ".join(row["name"] for row in unreachable)
        parts.append(
            '<p class="muted">Registered but not reachable through any scope: '
            f"<code>{esc(names)}</code>.</p>"
        )

    parts.append(
        '<p class="muted">Generated from the server-side tool registry '
        "(<code>zolai/agent/tools/registry.py</code>) — the same definitions the MCP "
        "<code>tools/list</code> response and the OpenAI <code>tools</code> array are "
        "rendered from. Regenerate with <code>zolai mcp landing --out &lt;path&gt;</code>.</p>"
    )
    if generated_at:
        parts.append(f'<p class="muted">Generated at {esc(generated_at)}.</p>')
    parts += ["</body>", "</html>", ""]
    return "\n".join(parts)


def write_landing(out: str | Path, *, generated_at: str | None = None) -> Path:
    """Write :func:`render_landing` to ``out`` (parents created) and return it."""
    path = Path(out).expanduser()
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_landing(generated_at=generated_at), encoding="utf-8")
    return path


def documented_tool_names(page: str) -> list[str]:
    """Tool names a rendered page documents, in page order.

    The page's own machine-readable marker (``data-tool="…"``), so the drift
    guard compares the *rendered output* against the registry rather than
    re-reading the same registry twice.
    """
    names: list[str] = []
    for chunk in page.split('data-tool="')[1:]:
        names.append(chunk.split('"', 1)[0])
    return names


def as_json() -> dict[str, Any]:
    """The same content as data (for a future machine-readable consumer)."""
    return {
        "server": {
            "name": protocol.SERVER_NAME,
            "version": protocol.SERVER_VERSION,
            "protocol_version": protocol.PROTOCOL_VERSION,
            "supported_protocol_versions": list(protocol.SUPPORTED_PROTOCOL_VERSIONS),
            "instructions": protocol.SERVER_INSTRUCTIONS,
            "endpoints": list(ENDPOINTS),
        },
        "tiers": {tier: sorted(allow_list(tier)) for tier in TIERS},
        "tools": tool_rows(),
    }


def dumps_json() -> str:
    """``as_json()`` as pretty JSON (stable key order, no time)."""
    return json.dumps(as_json(), indent=2, ensure_ascii=False, sort_keys=True)


def names_shown(tiers: Iterable[str] = TIERS) -> list[str]:
    """Registered names that a page renders for ``tiers`` (registry order)."""
    wanted = {tier for tier in tiers}
    return [name for name in names_for("admin") if tier_for(name) in wanted]


__all__ = [
    "ENDPOINTS",
    "TIERS",
    "TIER_BLURB",
    "as_json",
    "documented_tool_names",
    "dumps_json",
    "names_shown",
    "render_landing",
    "tier_for",
    "tool_rows",
    "write_landing",
]
