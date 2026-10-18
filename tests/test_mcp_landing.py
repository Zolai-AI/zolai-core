"""Landing page — generated from the registry, never transcribed.

The public tool page is a *rendering* of
:mod:`zolai.agent.tools.registry`: which tools exist, what each does and which
access tier each sits at all come from the same definitions the MCP
``tools/list`` response and the OpenAI ``tools`` array are rendered from.  These
tests are the drift guard — they compare the **rendered output** (its own
``data-tool``/``data-tier`` markers) against the live registry, so a tool added,
renamed or re-scoped in code cannot leave the page describing a surface that
does not exist, and the page cannot document a tool the server will not run.

The same generator is what ``zolai mcp landing --out …`` (and the deploy path)
writes, so the CLI test asserts the command and the renderer agree byte-for-byte.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from zolai.agent.tools import registry as registry_mod
from zolai.agent.tools.registry import SPECS, allow_list, names_for
from zolai.agent.tools.types import ToolSpec
from zolai.mcp import landing, protocol


def _expected_page_order() -> list[str]:
    """Registry order, grouped tier-by-tier (public → member → admin)."""
    return [
        name
        for tier in landing.TIERS
        for name in names_for("admin")
        if landing.tier_for(name) == tier
    ]


def _page_pairs(page: str) -> list[tuple[str, str]]:
    """``(name, tier)`` for every rendered row, in page order."""
    pairs: list[tuple[str, str]] = []
    for chunk in page.split('<tr data-tool="')[1:]:
        name = chunk.split('"', 1)[0]
        tier = chunk.split('data-tier="', 1)[1].split('"', 1)[0]
        pairs.append((name, tier))
    return pairs


class TestDriftGuard:
    def test_the_page_documents_exactly_the_registry(self) -> None:
        page = landing.render_landing()
        assert landing.documented_tool_names(page) == _expected_page_order()
        assert sorted(landing.documented_tool_names(page)) == sorted(names_for("admin"))

    def test_every_registered_tool_is_documented_exactly_once(self) -> None:
        names = landing.documented_tool_names(landing.render_landing())
        assert len(names) == len(set(names)) == len(SPECS)
        assert set(names) == set(SPECS)

    def test_each_row_carries_the_tier_the_server_enforces(self) -> None:
        pairs = _page_pairs(landing.render_landing())
        assert pairs == [(name, landing.tier_for(name)) for name in _expected_page_order()]

    def test_the_page_follows_the_registry_not_a_copy(self, monkeypatch) -> None:
        """Deleting a tool from the registry removes its row — no stale copy."""
        assert "grammar_check" in landing.documented_tool_names(landing.render_landing())
        monkeypatch.delitem(SPECS, "grammar_check")
        page = landing.render_landing()
        assert "grammar_check" not in landing.documented_tool_names(page)
        assert landing.documented_tool_names(page) == _expected_page_order()

    def test_a_new_registry_tool_appears_on_the_next_render(self, monkeypatch) -> None:
        fake = ToolSpec(
            name="zz_probe_tool",
            description="A probe tool that only exists in this test.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            title="Probe tool",
        )
        monkeypatch.setitem(SPECS, "zz_probe_tool", fake)
        # A tool is only reachable once a scope lists it; widen member+admin in
        # both the registry (drives row selection/order) and landing (drives the
        # derived tier) so the page has to pick it up from the registry.
        patched = _with_extra("zz_probe_tool", registry_mod.allow_list)
        monkeypatch.setattr(registry_mod, "allow_list", patched)
        monkeypatch.setattr(landing, "allow_list", patched)
        page = landing.render_landing()
        assert "zz_probe_tool" in landing.documented_tool_names(page)
        assert ("zz_probe_tool", "member") in _page_pairs(page)


def _with_extra(name: str, real):
    def patched(scope: str) -> frozenset[str]:
        base = real(scope)
        if scope in {"member", "admin"}:
            return frozenset(base) | {name}
        return base

    return patched


class TestPageContent:
    def test_the_count_line_matches_the_registry(self) -> None:
        page = landing.render_landing()
        counts = ", ".join(f"{len(allow_list(tier))} {tier}" for tier in landing.TIERS)
        assert f"{len(SPECS)} registered tools — {counts}" in page

    def test_the_version_block_matches_the_protocol(self) -> None:
        page = landing.render_landing()
        assert protocol.PROTOCOL_VERSION in page
        for version in protocol.SUPPORTED_PROTOCOL_VERSIONS:
            assert version in page

    def test_both_endpoints_are_published(self) -> None:
        page = landing.render_landing()
        for endpoint in landing.ENDPOINTS:
            assert endpoint in page

    def test_the_page_is_standalone(self) -> None:
        page = landing.render_landing()
        assert page.startswith("<!DOCTYPE html>")
        assert "<script" not in page  # no scripts
        assert 'src="' not in page  # no external assets
        assert "stylesheet" not in page

    def test_no_timestamp_by_default_and_one_when_asked(self) -> None:
        assert "Generated at" not in landing.render_landing()
        stamped = landing.render_landing(generated_at="2026-10-18T00:00:00Z")
        assert "Generated at 2026-10-18T00:00:00Z" in stamped

    def test_rendering_is_deterministic(self) -> None:
        assert landing.render_landing() == landing.render_landing()

    def test_unreachable_rows_are_called_out_not_silently_dropped(self, monkeypatch) -> None:
        fake = ToolSpec(
            name="zz_orphan",
            description="Registered but in no scope.",
            parameters={"type": "object", "properties": {}},
        )
        monkeypatch.setitem(SPECS, "zz_orphan", fake)
        page = landing.render_landing()
        # not reachable → no table row, but named explicitly
        assert "zz_orphan" not in landing.documented_tool_names(page)
        assert "Registered but not reachable" in page
        assert "zz_orphan" in page


class TestJson:
    def test_as_json_mirrors_the_registry(self) -> None:
        payload = landing.as_json()
        assert payload["server"]["name"] == protocol.SERVER_NAME
        assert payload["server"]["protocol_version"] == protocol.PROTOCOL_VERSION
        assert payload["server"]["supported_protocol_versions"] == list(
            protocol.SUPPORTED_PROTOCOL_VERSIONS
        )
        assert payload["tiers"] == {tier: sorted(allow_list(tier)) for tier in landing.TIERS}
        assert [row["name"] for row in payload["tools"]] == list(SPECS)

    def test_dumps_json_is_stable_and_valid(self) -> None:
        first = landing.dumps_json()
        assert json.loads(first) == landing.as_json()
        assert landing.dumps_json() == first


class TestCli:
    """The deploy path is exactly ``zolai mcp landing --out …``."""

    def test_landing_command_writes_the_rendered_page(self, tmp_path) -> None:
        from zolai.cli.main import app as zolai_app

        out = tmp_path / "nested" / "mcp-landing.html"
        result = CliRunner().invoke(zolai_app, ["mcp", "landing", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert out.read_text(encoding="utf-8") == landing.render_landing()
        assert (
            landing.documented_tool_names(out.read_text(encoding="utf-8")) == _expected_page_order()
        )

    def test_landing_command_honours_generated_at(self, tmp_path) -> None:
        from zolai.cli.main import app as zolai_app

        out = tmp_path / "landing.html"
        result = CliRunner().invoke(
            zolai_app,
            ["mcp", "landing", "--out", str(out), "--generated-at", "2026-10-18T00:00:00Z"],
        )
        assert result.exit_code == 0, result.output
        assert "Generated at 2026-10-18T00:00:00Z" in out.read_text(encoding="utf-8")

    def test_the_landing_command_is_addressable_as_a_subcommand(self) -> None:
        """``mcp landing`` must not collapse into a bare ``mcp`` command."""
        from zolai.cli.main import app as zolai_app

        result = CliRunner().invoke(zolai_app, ["mcp", "--help"])
        assert result.exit_code == 0, result.output
        assert "landing" in result.output
        assert "Commands" in result.output
