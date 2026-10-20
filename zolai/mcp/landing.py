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
:root {
  color-scheme: light dark;
  --space-xs: .25rem; --space-sm: .5rem; --space-md: 1rem; --space-lg: 1.5rem;
  --space-xl: 2rem; --space-2xl: 3rem;
  --radius-sm: .25rem; --radius-md: .5rem; --radius-lg: .75rem;
  --border-color: color-mix(in srgb, currentColor 20%, transparent);
  --muted-opacity: .7;
  --code-bg: color-mix(in srgb, currentColor 10%, transparent);
  --table-header-bg: color-mix(in srgb, currentColor 8%, transparent);
  --card-bg: color-mix(in srgb, currentColor 5%, transparent);
  --focus-ring: 0 0 0 2px color-mix(in srgb, currentColor 30%, transparent);
  --shadow-sm: 0 1px 2px color-mix(in srgb, currentColor 10%, transparent);
  --shadow-md: 0 4px 6px -1px color-mix(in srgb, currentColor 10%, transparent);
  --transition-fast: 150ms ease; --transition-normal: 250ms ease;
}

/* ==================== BASE & TYPOGRAPHY ==================== */
*, *::before, *::after { box-sizing: border-box; }
html { font-size: 100%; -webkit-text-size-adjust: 100%; }
body {
  font: clamp(15px, 1.6vw, 17px) / 1.6 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 0; min-width: 0; max-width: 100%;
  background: canvas; color: canvastext;
}
main { width: min(100% - 2*var(--space-md), 64rem); margin: 0 auto; padding: var(--space-lg) var(--space-md) var(--space-2xl); }
@media (min-width: 48rem) { main { padding: var(--space-xl) var(--space-lg) var(--space-2xl); } }
@media (min-width: 64rem) { main { padding: var(--space-2xl) var(--space-xl) var(--space-2xl); } }

h1 { font-size: clamp(1.5rem, 4vw, 2.25rem); font-weight: 700; line-height: 1.2; margin: 0 0 var(--space-sm); letter-spacing: -.02em; }
h2 { font-size: clamp(1.25rem, 3vw, 1.5rem); font-weight: 600; line-height: 1.3; margin: var(--space-2xl) 0 var(--space-md); color: inherit; }
h3 { font-size: clamp(1rem, 2.5vw, 1.125rem); font-weight: 600; margin: var(--space-lg) 0 var(--space-sm); }
p, li { max-width: 75ch; line-height: 1.65; margin: var(--space-sm) 0; }
a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
a:hover, a:focus { text-decoration-thickness: 2px; }
code, kbd, samp { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .9em; background: var(--code-bg); padding: .1em .35em; border-radius: var(--radius-sm); word-break: break-word; }
pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem; line-height: 1.55; background: var(--code-bg); padding: var(--space-md); border-radius: var(--radius-md); overflow-x: auto; margin: var(--space-md) 0; max-width: 100%; }
pre code { background: transparent; padding: 0; font-size: inherit; }

/* ==================== UTILITIES ==================== */
.muted { opacity: var(--muted-opacity); font-size: .92rem; }
.tier { display: inline-flex; align-items: center; font-size: .7rem; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; padding: .1rem .45rem; border-radius: var(--radius-sm); background: var(--table-header-bg); white-space: nowrap; }
.tier-public { background: color-mix(in srgb, green 15%, transparent); }
.tier-member { background: color-mix(in srgb, blue 15%, transparent); }
.tier-admin { background: color-mix(in srgb, purple 15%, transparent); }
.tier-unreachable { background: color-mix(in srgb, red 15%, transparent); }

/* ==================== ENDPOINTS LIST ==================== */
ul.endpoints { list-style: none; padding: 0; margin: var(--space-md) 0; display: flex; flex-wrap: wrap; gap: var(--space-sm); }
ul.endpoints li { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: var(--space-sm) var(--space-md); font-size: .9rem; min-width: 0; flex: 1 1 280px; }
ul.endpoints code { background: transparent; padding: 0; font-size: inherit; }

/* ==================== TABLES - RESPONSIVE ==================== */
.table-wrapper { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin: var(--space-md) 0; border: 1px solid var(--border-color); border-radius: var(--radius-lg); background: canvas; }
.table-wrapper::-webkit-scrollbar { height: 8px; }
.table-wrapper::-webkit-scrollbar-thumb { background: color-mix(in srgb, currentColor 30%, transparent); border-radius: 4px; }
table { border-collapse: collapse; width: 100%; min-width: 600px; font-size: .9rem; }
th, td { border: none; border-bottom: 1px solid var(--border-color); padding: var(--space-sm) var(--space-md); text-align: left; vertical-align: top; }
th { background: var(--table-header-bg); font-weight: 600; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; color: inherit; opacity: .85; position: sticky; top: 0; z-index: 1; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: var(--table-header-bg); }
td.name { white-space: nowrap; font-weight: 500; min-width: 160px; }
td.name code { background: transparent; padding: 0; font-size: .95em; }
td.description { max-width: 45ch; }
td.notes { max-width: 30ch; }
td.args code { background: transparent; padding: 0; font-size: .85em; }

/* ==================== MOBILE CARD VIEW (<= 480px) ==================== */
@media (max-width: 480px) {
  .table-wrapper { border: none; border-radius: 0; min-width: 0; overflow: visible; }
  .table-wrapper table, .table-wrapper thead, .table-wrapper tbody, .table-wrapper th, .table-wrapper td, .table-wrapper tr {
    display: block; width: 100%; box-sizing: border-box; }
  .table-wrapper thead { display: none; }
  .table-wrapper tr { margin-bottom: var(--space-lg); padding: var(--space-md); background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); }
  .table-wrapper td { border: none; padding: var(--space-xs) 0; display: flex; flex-wrap: wrap; gap: var(--space-sm); align-items: flex-start; min-height: 2.5rem; }
  .table-wrapper td::before { content: attr(data-label); font-weight: 600; font-size: .7rem; text-transform: uppercase; letter-spacing: .05em; color: inherit; opacity: .65; min-width: 80px; flex-shrink: 0; }
  .table-wrapper td.name { font-weight: 600; font-size: 1rem; padding-bottom: var(--space-sm); border-bottom: 1px solid var(--border-color); margin-bottom: var(--space-xs); }
  .table-wrapper td.name::before { display: none; }
  .table-wrapper td.tier .tier { font-size: .65rem; }
}

/* ==================== TABLET VIEW (481px - 768px) ==================== */
@media (min-width: 481px) and (max-width: 768px) {
  .table-wrapper { min-width: 0; }
  table { font-size: .85rem; }
  th, td { padding: var(--space-sm) var(--space-sm); }
  td.name { min-width: 140px; }
  td.description { max-width: 35ch; }
}

/* ==================== FOCUS & ACCESSIBILITY ==================== */
:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-sm); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }

/* ==================== PRINT ==================== */
@media print {
  .table-wrapper { overflow: visible; border: none; }
  .table-wrapper table { min-width: 100%; page-break-inside: auto; }
  .table-wrapper tr { page-break-inside: avoid; break-inside: avoid; }
  main { max-width: none; padding: 0; }
}
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
        parts.append('<div class="table-wrapper"><table>')
        parts.append(
            "<thead><tr><th>Tool</th><th>What it does</th>"
            "<th>Arguments</th><th>Notes</th></tr></thead><tbody>"
        )
        for row in tier_rows:
            parts.append(
                f'<tr data-tool="{esc(row["name"])}" data-tier="{esc(row["tier"])}">'
                f'<td class="name" data-label="Tool"><code>{esc(row["name"])}</code><br>'
                f'<span class="tier tier-{row["tier"]}">{esc(row["tier"])}</span>'
                f'<span class="title muted">{esc(row["title"])}</span></td>'
                f'<td data-label="Description">{esc(row["description"])}</td>'
                f'<td data-label="Arguments"><code>{esc(_arguments(row))}</code></td>'
                f'<td data-label="Notes" class="muted">{esc(_notes(row))}</td>'
                "</tr>"
            )
        parts.append("</tbody></table></div>")

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
