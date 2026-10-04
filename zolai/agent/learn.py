"""Learn phase (P3 §C) — proposal-only writes after a thumbs-up.

Master Prompt §36/§39: the LLM/agent **never writes a canonical table**.
This module's write surface is closed by design to exactly two proposal
stores:

- ``hypotheses`` (kind ``'agent'`` candidate rows) via
  :class:`~zolai.data.repositories.knowledge.HypothesisRepository`;
- ``foundation_review_queue`` via
  :class:`~zolai.data.repositories.foundation.FoundationReviewQueueRepository`
  — a human approves before anything reaches canonical knowledge.

``tests/test_agent_runs.py::TestLearnIsProposalOnly`` source-scans this
package for raw write SQL: any ``INSERT/UPDATE/DELETE`` outside
``agent_runs`` fails the suite.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Tables this package may ever name in write SQL (source-scan contract).
ALLOWED_SQL_WRITE_TABLES: frozenset[str] = frozenset({"agent_runs"})

#: Proposal stores written through the repository layer (never raw SQL here).
PROPOSAL_TABLES: tuple[str, ...] = ("hypotheses", "foundation_review_queue")


def learn_from_run(run_id: int, score: float, *, engine: Any = None) -> dict[str, Any]:
    """Turn an accepted run (``score >= 1``) into a reviewable candidate.

    Returns:
        ``{"learned": bool, "reason"|"hypothesis_id"|"review_item_id": ...}``.
    """
    from . import store

    if score < 1:
        return {"learned": False, "reason": "score_below_threshold"}

    if engine is None:
        from ..data.repositories import get_engine

        engine = get_engine()

    run = store.get_run(int(run_id), engine=engine)
    if run is None:
        return {"learned": False, "reason": "run_not_found"}

    answer = str(run.get("answer") or "").strip()
    goal = str(run.get("goal") or "").strip()
    evidence = run.get("evidence") or []

    hypothesis_id: int | None = None
    review_item_id: int | None = None

    try:
        from ..data.repositories.foundation import FoundationReviewQueueRepository
        from ..data.repositories.knowledge import HypothesisRepository

        hyp_repo = HypothesisRepository(engine)
        hypothesis_id = int(
            hyp_repo.create(
                {
                    "kind": "agent",
                    "subject": f"agent_run:{run_id}",
                    "predicate": "accepted_answer",
                    "object": answer[:500] or goal[:500],
                    "probability": 1.0,
                    "confidence": 0.5,
                    "evidence_count": len(evidence),
                    "source_count": 1,
                    "extras": {
                        "goal": goal,
                        "run_id": int(run_id),
                        "feedback_score": float(score),
                        "provider": run.get("provider"),
                        "model": run.get("model"),
                    },
                    "status": "OBSERVED",
                },
                user="agent",
            )
        )
        queue_repo = FoundationReviewQueueRepository(engine)
        review_item_id = int(
            queue_repo.add_to_queue(
                "agent_run",
                f"run:{run_id}",
                priority=1,
                user="agent",
            )
        )
    except Exception as exc:
        logger.exception("learn phase failed for run %s", run_id)
        return {
            "learned": False,
            "reason": "proposal_write_failed",
            "detail": str(exc),
            "hypothesis_id": hypothesis_id,
        }

    return {
        "learned": True,
        "hypothesis_id": hypothesis_id,
        "review_item_id": review_item_id,
        "tables": list(PROPOSAL_TABLES),
    }
