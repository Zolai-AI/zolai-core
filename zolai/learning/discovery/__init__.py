"""Phase 3 — Linguistic Discovery package (Master Prompt §36).

Five capabilities writing evidence-linked hypotheses:
- POS hypotheses (kind='pos')
- Morphological relations (kind='morph_relation')
- Collocations (kind='collocation')
- Sentence patterns → grammar_patterns (disc_sp_* namespace)
- Grammar phenomena → grammar_patterns (disc_g_* namespace)

All writes are offline/rule-mode, capped, idempotent, and emit only
OBSERVED/CANDIDATE status via the shared discovery status guard.
"""

from .collocation import build_collocation_hypotheses
from .evidence import (
    EVIDENCE_TIER_MAP,
    upsert_evidence,
    upsert_evidence_bulk,
)
from .grammar import build_grammar_hypotheses
from .morphology import build_morphology_hypotheses
from .pipeline import DiscoverySummary, build_discovery
from .pos import build_pos_hypotheses
from .sentence_patterns import build_sentence_pattern_hypotheses

__all__ = [
    "DiscoverySummary",
    "EVIDENCE_TIER_MAP",
    "build_collocation_hypotheses",
    "build_discovery",
    "build_grammar_hypotheses",
    "build_morphology_hypotheses",
    "build_pos_hypotheses",
    "build_sentence_pattern_hypotheses",
    "upsert_evidence",
    "upsert_evidence_bulk",
]
