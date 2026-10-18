"""``zolai mcp`` — the MCP surface from the command line.

One subcommand today: ``landing``, which renders the public tool landing page
**from the server-side registry** (never a transcribed list) so the page a
visitor reads and the tool list a client receives can never disagree.  The
deploy path runs exactly this command
(``zolai mcp landing --out /var/www/zolai-mcp/index.html``).
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich import print as rprint

__all__ = ["mcp_app"]

mcp_app = typer.Typer(
    name="mcp",
    help="🔌 MCP surface — render the generated tool landing page.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@mcp_app.command("landing")
def landing(
    out: Path = typer.Option(
        Path("mcp-landing.html"),
        "--out",
        "-o",
        help="File to write the standalone HTML page to (parents are created).",
    ),
    generated_at: str = typer.Option(
        "",
        "--generated-at",
        help="Optional ISO timestamp to record in the page. Omitted by default so two "
        "builds of the same registry are byte-identical.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Render the MCP tool landing page from the tool registry.

    Every row is derived from :mod:`zolai.agent.tools.registry` (the same
    definitions ``tools/list`` and the OpenAI ``tools`` array are rendered
    from), including each tool's real access tier.
    """
    _setup_logging(verbose)
    from ..agent.tools.registry import SPECS, allow_list
    from ..mcp import protocol
    from ..mcp.landing import TIERS, write_landing

    path = write_landing(out, generated_at=generated_at or None)
    counts = " · ".join(f"{len(allow_list(tier))} {tier}" for tier in TIERS)
    rprint(f"[green]✓ Wrote {path.resolve()}[/green]")
    rprint(
        f"  {len(SPECS)} registered tools ({counts}) · "
        f"protocol {protocol.PROTOCOL_VERSION}"
    )
