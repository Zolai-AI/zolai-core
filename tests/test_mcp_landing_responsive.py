"""Landing page responsive CSS — no document-level horizontal overflow.

`zolai/mcp/landing.py` carries the whole stylesheet inline in ``_STYLE``. The
table used to declare ``min-width: 600px`` and nothing neutralised it until the
``@media print`` block, so on a 320–480px viewport the page itself scrolled
horizontally even though the card fallback rendered correctly (the card block
sets ``.table-wrapper { overflow: visible }``, so the 600px table box escaped
any containment). This module parses ``_STYLE`` with a small cascade resolver
and asserts, per viewport width, that the *winning* ``min-width`` for the
table cannot force the document wider than the viewport.

Internal scrolling of the table inside ``.table-wrapper`` is fine — page-level
overflow is not.
"""

from __future__ import annotations

import re

from zolai.mcp import landing

#: Widths the page must be free of document-level overflow at.
VIEWPORT_WIDTHS: tuple[int, ...] = (
    320,
    375,
    414,
    480,
    481,
    599,
    600,
    768,
    1024,
    1440,
)

#: The band where the card fallback owns the layout (``<= 480px``).
CARD_MAX_PX = 480

#: The band where the real table is shown but narrow (``481 – 768px``).
TABLET_MAX_PX = 768


# ---------------------------------------------------------------------------
# A very small CSS cascade: enough to answer "what min-width does `table` get
# at width W", nothing more. Deliberately literal — it understands exactly the
# constructs `_STYLE` uses and raises on anything else, so a stylesheet change
# that outgrows the resolver fails loudly instead of silently passing.
# ---------------------------------------------------------------------------


def _rules(
    css: str, media: str | None = None
) -> list[tuple[str | None, str, dict[str, str]]]:
    """``[(media-condition-or-None, selector, declarations)]`` for a stylesheet.

    One brace-balanced walk: an ``@media`` recurses with its condition attached
    to every rule it contains, an unknown ``@``-rule is skipped, and anything
    unparseable raises rather than being quietly ignored — so a stylesheet that
    outgrows this resolver fails the test loudly instead of passing vacuously.
    """
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    out: list[tuple[str | None, str, dict[str, str]]] = []
    i, n = 0, len(css)
    while i < n:
        open_at = css.find("{", i)
        if open_at == -1:
            trailing = css[i:].strip()
            if trailing:
                raise ValueError(f"unbraced CSS left over: {trailing[:60]!r}")
            break
        prelude = " ".join(css[i:open_at].split())
        depth, j = 1, open_at + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        if depth:
            raise ValueError(f"unbalanced braces in {prelude!r}")
        body = css[open_at + 1 : j - 1]
        if prelude.startswith("@media"):
            if media is not None:
                raise ValueError("nested @media is not supported here")
            out.extend(_rules(body, media=prelude[len("@media") :].strip()))
        elif prelude.startswith("@"):
            pass  # @import/@keyframes/… — not layout-relevant here
        else:
            decls: dict[str, str] = {}
            for decl in body.split(";"):
                if ":" not in decl:
                    continue
                prop, _, value = decl.partition(":")
                decls[prop.strip().lower()] = value.strip()
            if prelude and decls:
                out.append((media, prelude, decls))
        i = j
    return out


def _media_matches(condition: str | None, width: int) -> bool:
    """Whether a media condition holds for a viewport width (px only)."""
    if condition is None or condition in ("", "all"):
        return True
    if condition == "print":
        return False
    for part in re.split(r"\band\b", condition):
        part = part.strip().strip("()")
        m = re.fullmatch(r"(min|max)-width:\s*([\d.]+)(px|rem|em)", part)
        if not m:
            raise ValueError(f"unsupported media feature: {part!r}")
        bound = float(m.group(2))
        if m.group(3) in ("rem", "em"):
            bound *= 16  # `html { font-size: 100% }` → 1rem = 16px
        if m.group(1) == "min" and width < bound:
            return False
        if m.group(1) == "max" and width > bound:
            return False
    return True


def _selector_matches(selector: str, chain: tuple[str, ...]) -> bool:
    """Whether ``selector`` applies to the innermost element of ``chain``.

    ``chain`` is the ancestor path from outermost to innermost compound
    selector, e.g. ``(".table-wrapper", "td.name")``. Descendant combinators
    only; compounds must match exactly (this stylesheet uses no classes with
    extra tokens on the left-hand side). Comma-separated selector lists are
    tried one alternative at a time.
    """
    for alternative in selector.split(","):
        if _chain_matches(alternative.strip(), chain):
            return True
    return False


def _chain_matches(selector: str, chain: tuple[str, ...]) -> bool:
    parts = [p for p in re.split(r"\s+|>", selector) if p]
    if not parts or parts[-1] != chain[-1]:
        return False
    rest = parts[:-1]
    idx = len(chain) - 2  # walk ancestors right-to-left
    for part in reversed(rest):
        while idx >= 0 and chain[idx] != part:
            idx -= 1
        if idx < 0:
            return False
        idx -= 1
    return True


def _winning_decls(
    style: str, width: int, chain: tuple[str, ...]
) -> dict[str, str]:
    """Last-winning declarations for ``chain`` at a viewport width.

    Source order only — that is the correct tie-break for this stylesheet,
    where every override either raises specificity (``.table-wrapper table``
    over ``table``) or appears later.
    """
    winner: dict[str, str] = {}
    seen = False
    for condition, candidate, decls in _rules(style):
        if not _media_matches(condition, width):
            continue
        if _selector_matches(candidate, chain):
            expanded = dict(decls)
            # `overflow: <x>` is a shorthand for overflow-x/overflow-y; without
            # this the resolver would believe an old `overflow-x: auto` still
            # wins under a later `overflow: visible`.
            if "overflow" in expanded:
                for side in ("overflow-x", "overflow-y"):
                    expanded.setdefault(side, expanded["overflow"])
            winner.update(expanded)
            seen = True
    if not seen:
        raise AssertionError(f"no rule for {' '.join(chain)!r} at {width}px")
    return winner


def _px(value: str) -> float:
    """Resolve a px/rem length; percentages and ``auto`` are not lengths."""
    m = re.fullmatch(r"([\d.]+)(px|rem|em)", value)
    if not m:
        raise ValueError(f"not an absolute length: {value!r}")
    return float(m.group(1)) * (16 if m.group(2) in ("rem", "em") else 1)


class TestTableMinWidthNeverForcesPageOverflow:
    """The `min-width: 600px` table rule must lose below ~600px."""

    def test_winning_table_min_width_never_widens_the_document(self) -> None:
        """At every width: the table fits, or the wrapper contains the scroll."""
        for width in VIEWPORT_WIDTHS:
            table = _winning_decls(landing._STYLE, width, (".table-wrapper", "table"))
            wrapper = _winning_decls(landing._STYLE, width, (".table-wrapper",))
            raw = table.get("min-width")
            assert raw is not None, f"table has no min-width at {width}px"
            if raw in ("auto", "0", "0px") or _px(raw) <= width:
                continue
            # A floor wider than the viewport is only safe if the scroll is
            # trapped inside .table-wrapper instead of reaching the page.
            contains = wrapper.get("overflow-x") in ("auto", "scroll") or (
                wrapper.get("overflow") in ("auto", "scroll")
            )
            assert contains, (
                f"at {width}px table min-width: {raw} exceeds the viewport and "
                f".table-wrapper does not scroll horizontally ({wrapper}) — the "
                f"page itself would scroll"
            )

    def test_the_card_band_neutralises_the_600px_floor(self) -> None:
        """<= 480px is the card layout: a 600px table box must not survive."""
        for width in (320, 375, 414, CARD_MAX_PX):
            decls = _winning_decls(landing._STYLE, width, (".table-wrapper", "table"))
            assert decls["min-width"] in ("0", "0px"), (
                f"at {width}px the card layout still gets "
                f"table min-width: {decls['min-width']}"
            )

    def test_the_tablet_band_keeps_the_floor_but_scrolls_inside_the_wrapper(
        self,
    ) -> None:
        """481-768px shows the real table; the scroll must stay in the wrapper."""
        for width in (481, 599, 600, TABLET_MAX_PX):
            wrapper = _winning_decls(landing._STYLE, width, (".table-wrapper",))
            assert wrapper.get("overflow-x") == "auto" or wrapper.get(
                "overflow"
            ) in ("auto", "scroll"), (
                f"at {width}px .table-wrapper does not contain the table "
                f"horizontally ({wrapper})"
            )

    def test_the_base_rule_is_still_the_declared_floor(self) -> None:
        """Guard the other direction: the fix must not just delete the rule."""
        decls = _winning_decls(landing._STYLE, 1440, (".table-wrapper", "table"))
        assert decls["min-width"] == "600px"


class TestNameCellDoesNotRefuseToWrap:
    """`td.name { white-space: nowrap }` was a second overflow source."""

    def test_no_rule_sets_nowrap_on_the_name_cell(self) -> None:
        for condition, selector, decls in _rules(landing._STYLE):
            if not re.search(r"(^|[\s,])td\.name\b", selector):
                continue
            assert decls.get("white-space") != "nowrap", (
                f"{selector!r} (media {condition!r}) sets white-space: "
                f"nowrap — a nowrap column is a horizontal-overflow source"
            )

    def test_the_card_band_lets_the_name_cell_shrink(self) -> None:
        """In card view td.name is a full-width row: no floor, no nowrap."""
        for width in (320, CARD_MAX_PX):
            decls = _winning_decls(landing._STYLE, width, (".table-wrapper", "td.name"))
            assert decls.get("min-width") in ("0", "0px"), decls
            assert decls.get("white-space") == "normal", decls


class TestLongTokensCanBreakInCardCells:
    """A bare text node in a flex `td` must not set the cell's min-content width."""

    def test_card_cells_allow_anywhere_wrapping(self) -> None:
        for width in (320, CARD_MAX_PX):
            decls = _winning_decls(landing._STYLE, width, (".table-wrapper", "td"))
            assert decls.get("overflow-wrap") in ("anywhere", "break-word"), (
                f"at {width}px card cells cannot break long tokens: {decls}"
            )


class TestPreservedBehaviour:
    """The fix must not disturb what already worked."""

    def test_print_still_fits_the_table_to_the_page(self) -> None:
        printed = {
            selector: decls
            for condition, selector, decls in _rules(landing._STYLE)
            if condition == "print"
        }
        assert printed, "the @media print block is gone"
        table_rules = {
            sel: d for sel, d in printed.items() if sel.endswith("table")
        }
        assert any(d.get("min-width") == "100%" for d in table_rules.values()), (
            f"print no longer fits the table to the page: {printed}"
        )

    def test_focus_visible_styles_are_still_present(self) -> None:
        assert ":focus-visible" in landing._STYLE

    def test_no_dark_mode_palette_was_added(self) -> None:
        """The page relies on `color-scheme` + color-mix; no half dark mode."""
        assert "@media (prefers-color-scheme" not in landing._STYLE

    def test_the_page_still_renders(self) -> None:
        page = landing.render_landing()
        assert page.count("table-wrapper") >= 3
        assert len(landing.documented_tool_names(page)) == len(landing.tool_rows())
