"""Offline evaluation for Zolai outputs.

Metrics are dependency-free pure Python, so this runs anywhere (including CI)
with no model weights::

    from zolai.eval import evaluate
    scores = evaluate(sets="db:eval_v1")

Sets live in the canonical database (``eval_sets`` / ``eval_cases``, see
:mod:`zolai.eval.store`); the bundled JSONL files are import/export
interchange. Model training is intentionally out of scope — this package only
measures.
"""

from __future__ import annotations

from .baseline import below_floor, load_baseline
from .datasets import SMOKE, DbRef, load_dataset, resolve_set
from .metrics import (
    GrammarMetrics,
    MorphologyMetrics,
    POSMetrics,
    TokenizationMetrics,
    compute_grammar_metrics,
    compute_morphology_metrics,
    compute_pos_metrics,
    compute_tokenization_metrics,
    qa_term_recall,
    translation_bleu,
    translation_chrf,
    zvs_compliance_rate,
)
from .store import (
    ALL_SETS,
    GOLD_SETS,
    EvalItem,
    EvalSet,
    add_eval_item,
    create_eval_set,
    ensure_schema,
    export_eval_set_to_jsonl,
    export_set_to_jsonl,
    fetch_cases,
    get_eval_items,
    get_eval_set,
    import_jsonl_to_set,
    init_eval_db,
    list_eval_sets,
    list_kinds,
    list_sets,
    load_gold_from_jsonl,
    seed_sets,
)

__all__ = [
    # Baseline gating
    "SMOKE",
    "below_floor",
    "load_baseline",
    # Datasets
    "DbRef",
    "load_dataset",
    "resolve_set",
    # Lane metrics
    "qa_term_recall",
    "translation_bleu",
    "translation_chrf",
    "zvs_compliance_rate",
    "evaluate",
    # Task metrics (ZolaiBench v0.1)
    "GrammarMetrics",
    "MorphologyMetrics",
    "POSMetrics",
    "TokenizationMetrics",
    "compute_grammar_metrics",
    "compute_morphology_metrics",
    "compute_pos_metrics",
    "compute_tokenization_metrics",
    # Store
    "ALL_SETS",
    "EvalItem",
    "EvalSet",
    "GOLD_SETS",
    "add_eval_item",
    "create_eval_set",
    "ensure_schema",
    "export_eval_set_to_jsonl",
    "export_set_to_jsonl",
    "fetch_cases",
    "get_eval_items",
    "get_eval_set",
    "import_jsonl_to_set",
    "init_eval_db",
    "list_eval_sets",
    "list_kinds",
    "list_sets",
    "load_gold_from_jsonl",
    "seed_sets",
]

def evaluate(sets: str = SMOKE, *, base_dir: str | None = None) -> dict[str, float]:
    """Evaluate a set and return every metric whose lane has data.

    Args:
        sets: ``"smoke"``, ``"db"``, ``"db:<name>"``, or a path/base prefix for
            :func:`load_dataset` to resolve.
        base_dir: Optional override directory for the ``smoke`` fixtures.

    Returns:
        Mapping of metric name to score. Lanes with no records are omitted, so
        a missing set never scores ``0.0`` and cannot trip a release gate.
    """
    data = load_dataset(sets, base_dir=base_dir)
    result: dict[str, float] = {}
    zvs = data.get("zvs")
    if isinstance(zvs, list):
        result["zvs_compliance_rate"] = zvs_compliance_rate(zvs)
    pair = data.get("translation")
    if isinstance(pair, tuple):
        hyps, refs = pair
        result["translation_bleu"] = translation_bleu(hyps, refs)
        result["translation_chrf"] = translation_chrf(hyps, refs)
    pair = data.get("qa")
    if isinstance(pair, tuple):
        hyps, refs = pair
        result["qa_term_recall"] = qa_term_recall(hyps, refs)
    return result
