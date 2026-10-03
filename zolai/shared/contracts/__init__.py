"""Phase 1 knowledge contracts — the single canonical home (Master Prompt §36).

Eleven storage-facing contracts plus the shared lifecycle enum and the
tier-weighted confidence helper:

1. ``Word``                      2. ``WordForm``              3. ``Observation``
4. ``Evidence``                  5. ``Hypothesis``            6. ``KnowledgeClaim``
7. ``GrammarPattern``            8. ``MorphologicalRelation`` 9. ``POSHypothesis``
10. ``Source``                   11. ``KnowledgeVersion``

Ownership rules: contracts live in ``shared`` (cross-domain imports go through
shared), ZVS stays in ``zolai/zvs/rules_data.py``, and no behaviour or API
surface changes with this package — it is types + validation only.
"""

from .base import (
    ALLOWED_TRANSITIONS,
    GATED_STATUSES,
    EvidenceGateError,
    InvalidTransition,
    KnowledgeContract,
    KnowledgeStatus,
    confidence_from_evidence,
    validate_transition,
)
from .claim import KnowledgeClaim
from .evidence import Evidence, Observation
from .hypothesis import CollocationHypothesis, Hypothesis, MorphologicalRelation, POSHypothesis
from .lexicon import Word, WordForm
from .pattern import GrammarPattern
from .source import Source
from .version import KnowledgeVersion

__all__ = [
    "ALLOWED_TRANSITIONS",
    "GATED_STATUSES",
    "CollocationHypothesis",
    "Evidence",
    "EvidenceGateError",
    "GrammarPattern",
    "Hypothesis",
    "InvalidTransition",
    "KnowledgeClaim",
    "KnowledgeContract",
    "KnowledgeStatus",
    "KnowledgeVersion",
    "MorphologicalRelation",
    "Observation",
    "POSHypothesis",
    "Source",
    "Word",
    "WordForm",
    "confidence_from_evidence",
    "validate_transition",
]
