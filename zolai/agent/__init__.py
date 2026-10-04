"""Agent runtime (P3) — marker-protocol tool loop + run phases.

Distinct from the legacy ``zolai/agents/`` package (untouched) and the
orphaned ``zolai/cli/agent.py`` (unregistered): this package owns the new
``zolai agent`` CLI, the ``/api/v1/agent`` API surface and the 24th engine
spec (``zolai.agent.orchestrator:run_agent_goal``).

Phases: ``research → build → review → shipped`` on run, ``learn`` only on a
thumbs-up feedback (proposal writes into ``hypotheses`` + the foundation
review queue — never a canonical table, Master Prompt §36/§39).
"""

from __future__ import annotations

from typing import Any

__all__ = ["run_agent_goal"]


def run_agent_goal(goal: str, **kwargs: Any) -> dict[str, Any]:
    """Lazy re-export so ``zolai.agent:run_agent_goal`` imports stay cheap."""
    from .orchestrator import run_agent_goal as _run

    return _run(goal, **kwargs)
