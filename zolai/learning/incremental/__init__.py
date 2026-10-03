"""Phase 5 — Incremental Learning (Master Prompt §36).

Incremental pipeline: new corpus ingestion → hash-based change detection →
process only affected records → update statistics/hypotheses/claims →
evaluate → publish new knowledge version. Supports rollback via versions.
"""

from .change_detector import ChangeSet, detect_changes
from .processor import ProcessingSummary, process_changeset
from .knowledge_updater import UpdateSummary, update_knowledge_from_changes
from .regression import RegressionReport, run_regression_checks
from .pipeline import PipelineResult, run_incremental_pipeline

__all__ = [
    "ChangeSet",
    "detect_changes",
    "ProcessingSummary",
    "process_changeset",
    "UpdateSummary",
    "update_knowledge_from_changes",
    "RegressionReport",
    "run_regression_checks",
    "PipelineResult",
    "run_incremental_pipeline",
]
