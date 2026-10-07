"""ZolaiBench v0.1 — Evaluation Framework.

This package provides:
- Metrics: tokenization, POS, morphology, grammar evaluation
- Store: DB-first evaluation set storage (zolai_eval.db)
- Runner: CLI for running evaluations
"""

from .metrics import (
    GrammarMetrics,
    MorphologyMetrics,
    POSMetrics,
    TokenizationMetrics,
    compute_grammar_metrics,
    compute_morphology_metrics,
    compute_pos_metrics,
    compute_tokenization_metrics,
)
from .store import (
    GOLD_SETS,
    EvalItem,
    EvalSet,
    add_eval_item,
    create_eval_set,
    export_eval_set_to_jsonl,
    get_eval_items,
    get_eval_set,
    init_eval_db,
    list_eval_sets,
    load_gold_from_jsonl,
)

__all__ = [
    # Metrics
    "TokenizationMetrics",
    "POSMetrics",
    "MorphologyMetrics",
    "GrammarMetrics",
    "compute_tokenization_metrics",
    "compute_pos_metrics",
    "compute_morphology_metrics",
    "compute_grammar_metrics",
    # Store
    "EvalSet",
    "EvalItem",
    "init_eval_db",
    "create_eval_set",
    "add_eval_item",
    "get_eval_set",
    "list_eval_sets",
    "get_eval_items",
    "load_gold_from_jsonl",
    "export_eval_set_to_jsonl",
    "GOLD_SETS",
]
