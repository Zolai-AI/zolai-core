"""Phase 4 — Knowledge Engine (Master Prompt §36).

Promotes machine-discovered hypotheses into audited, evidence-backed knowledge claims
with consensus-driven confidence, human review queues, and versioned snapshots.

All writes go through repositories; no LLM→canonical direct writes.
"""

from .promotion import promote_hypotheses_to_claims
from .consensus import compute_claim_consensus
from .review import ReviewQueue
from .versioning import create_knowledge_version, list_knowledge_versions, get_knowledge_version

__all__ = [
    "promote_hypotheses_to_claims",
    "compute_claim_consensus",
    "ReviewQueue",
    "create_knowledge_version",
    "list_knowledge_versions",
    "get_knowledge_version",
]
