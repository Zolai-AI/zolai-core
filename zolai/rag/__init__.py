"""Phase 6 — RAG (Master Prompt §36).

Structured retrieval over canonical knowledge:
- Dictionary entries
- Word attestations
- Sentences (Bible, translations, phrases)
- Grammar patterns
- Morphology
- Collocations
- Knowledge claims & evidence
- Knowledge Graph

Hybrid search: vector + lexical fallback.
"""

from .retrieve import UnifiedRetriever, EvidencePack
from .evidence import rank_evidence, evidence_to_pack
from .build import build_knowledge_vectors

__all__ = [
    "UnifiedRetriever",
    "EvidencePack",
    "rank_evidence",
    "evidence_to_pack",
    "build_knowledge_vectors",
]
