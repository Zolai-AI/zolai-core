"""Declarative tool families — a *contract* per tool, with its handler.

A *family* is a set of tools generated from an upstream declaration rather
than hand-written one by one.  The ``foundation_*`` family is the original:
each typed ``/api/v1/foundation/*`` route is one
:class:`~zolai.api.foundation_router.FoundationToolContract`, and the family
pairs three things per contract —

- the **declaration** (the contract's ``operation_id``) — the reason the tool
  exists at all;
- the **model** (the route's Pydantic request body) — the tool's input schema;
- the **handler** (the in-process call the route makes) — what actually runs.

The whole point of the pairing is that a tool can never quietly disagree with
its contract.  Hand-writing that triple is exactly how a family drifts: a new
contract is declared and published as a tool with **no handler** (the surface
advertises a tool that errors on call), or a handler is written for a contract
that was never declared (dead code the surface never offers).
:func:`build_family` therefore asserts the three-way coverage **both ways**,
at the moment the declaring module is imported, so either drift is a start-up
failure instead of a production surprise.

The registry-wide half of the same invariant — every registered spec has a
handler — lives in :mod:`zolai.agent.tools.executor`, the one module that can
see both tables; :func:`assert_handlers_cover_specs` is the shared check.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .types import ToolSpec


class ToolFamilyError(RuntimeError):
    """A declared family is out of step with its models or handlers."""


def assert_tables_cover(
    family: str,
    label: str,
    declared: Iterable[str],
    table: Mapping[str, Any],
) -> None:
    """Raise unless ``table``'s keys are exactly ``declared`` (both directions).

    Both directions matter: a *missing* key publishes a tool that cannot run, an
    *extra* key is code the surface never offers.
    """
    want = set(declared)
    have = set(table)
    missing = sorted(want - have)
    extra = sorted(have - want)
    if missing or extra:
        raise ToolFamilyError(
            f"{family} tool family out of step with its declarations: "
            f"{label} missing={missing} extra={extra}"
        )


def assert_handlers_cover_specs(
    specs: Mapping[str, ToolSpec],
    handlers: Mapping[str, Callable[[dict[str, Any]], Any]],
    *,
    subject: str = "registry",
) -> None:
    """Raise unless ``handlers`` covers ``specs`` exactly (both directions).

    ``specs``/``handlers`` are passed in rather than imported so the same check
    serves the registry-wide table and any per-family table, and so a test can
    exercise it on a deliberately broken pair.
    """
    want = set(specs)
    have = set(handlers)
    missing = sorted(want - have)
    extra = sorted(have - want)
    if missing or extra:
        raise ToolFamilyError(
            f"{subject}: every declared tool needs a handler and every handler "
            f"needs a tool — missing_handler={missing} orphan_handler={extra}"
        )


@dataclass(frozen=True)
class ToolFamily:
    """One generated family: its specs, its handlers and its ordered names."""

    name: str
    specs: dict[str, ToolSpec]
    handlers: dict[str, Callable[[dict[str, Any]], Any]]
    names: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.specs)


def build_family(
    *,
    family: str,
    declared: Iterable[str],
    models: Mapping[str, Any],
    handlers: Mapping[str, Callable[[dict[str, Any]], Any]],
    name_of: Callable[[str], str],
    spec_of: Callable[[str, Any, str], ToolSpec],
) -> ToolFamily:
    """Build a family from its declaration, asserting full three-way coverage.

    Args:
        family: name used in error messages (``"foundation"``).
        declared: the family's declarations, **in publication order** — the
            built names follow it, so the order here is the order a client sees.
        models: declaration → the input contract (Pydantic model, schema
            source, …).  Asserted to cover ``declared`` exactly.
        handlers: declaration → the callable that runs it.  Asserted to cover
            ``declared`` exactly.
        name_of: declaration → registered tool name.
        spec_of: ``(declaration, model, tool_name)`` → the spec to publish.

    Raises:
        ToolFamilyError: a declaration has no model/handler, or a model/handler
            exists for no declaration.
    """
    order = tuple(declared)
    assert_tables_cover(family, "input model", order, models)
    assert_tables_cover(family, "handler", order, handlers)
    specs: dict[str, ToolSpec] = {}
    for key in order:
        tool_name = name_of(key)
        specs[tool_name] = spec_of(key, models[key], tool_name)
    return ToolFamily(
        name=family,
        specs=specs,
        handlers={name_of(key): handler for key, handler in handlers.items()},
        names=tuple(specs),
    )


__all__ = [
    "ToolFamily",
    "ToolFamilyError",
    "assert_handlers_cover_specs",
    "assert_tables_cover",
    "build_family",
]
