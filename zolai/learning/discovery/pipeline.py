"""Discovery pipeline orchestration (Phase 3 §36).

5-capability orchestration: POS, morphology, collocation, sentence_patterns, grammar.
Caps, idempotent, summary dict. CLI entrypoint via `zolai discovery build`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from .pos import build_pos_hypotheses
from .morphology import build_morphology_hypotheses
from .collocation import build_collocation_hypotheses
from .sentence_patterns import build_sentence_pattern_hypotheses
from .grammar import build_grammar_hypotheses

# Default caps per capability (plan defaults)
DEFAULT_CAPS: dict[str, int] = {
    "pos": 5000,
    "morphology": 5000,
    "collocation": 1000,
    "sentence_patterns": 500,
    "grammar": 500,
}

# Valid capabilities
VALID_CAPABILITIES = ["pos", "morphology", "collocation", "sentence_patterns", "grammar"]


@dataclass
class DiscoverySummary:
    """Summary of a discovery build run."""
    capabilities_run: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    pos: dict[str, Any] = field(default_factory=dict)
    morphology: dict[str, Any] = field(default_factory=dict)
    collocation: dict[str, Any] = field(default_factory=dict)
    sentence_patterns: dict[str, Any] = field(default_factory=dict)
    grammar: dict[str, Any] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_version": "phase3-v1",
            "capabilities_run": self.capabilities_run,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "pos": self.pos,
            "morphology": self.morphology,
            "collocation": self.collocation,
            "sentence_patterns": self.sentence_patterns,
            "grammar": self.grammar,
            "status_counts": self.status_counts,
        }


def build_discovery(
    engine: Engine,
    capabilities: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    caps: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run the 5-capability discovery pipeline.

    Args:
        engine: SQLAlchemy engine (canonical data/zolai.db)
        capabilities: List of capabilities to run (default: all 5)
        limit: Max source rows per capability (default: unlimited)
        dry_run: If True, report what would be done without writing
        caps: Override caps per capability

    Returns:
        DiscoverySummary as dict.
    """
    start = time.time()
    caps = {**DEFAULT_CAPS, **(caps or {})}
    caps_to_run = capabilities or VALID_CAPABILITIES

    # Validate capabilities
    invalid = [c for c in caps_to_run if c not in VALID_CAPABILITIES]
    if invalid:
        raise ValueError(f"Invalid capabilities: {invalid}. Valid: {VALID_CAPABILITIES}")

    summary = DiscoverySummary()
    summary.dry_run = dry_run
    summary.capabilities_run = caps_to_run
    total_status: dict[str, int] = {}

    # Dispatch table
    builders = {
        "pos": build_pos_hypotheses,
        "morphology": build_morphology_hypotheses,
        "collocation": build_collocation_hypotheses,
        "sentence_patterns": build_sentence_pattern_hypotheses,
        "grammar": build_grammar_hypotheses,
    }

    for cap in caps_to_run:
        cap_limit = min(limit, caps[cap]) if limit else caps[cap]
        builder = builders[cap]

        try:
            result = builder(engine, limit=cap_limit, dry_run=dry_run)
            setattr(summary, cap, result)

            # Aggregate status counts
            for status, count in result.get("status_counts", {}).items():
                total_status[status] = total_status.get(status, 0) + count

        except Exception as exc:
            # Log and continue with other capabilities
            result = {
                "capability": cap,
                "error": str(exc),
            }
            setattr(summary, cap, result)

    summary.status_counts = total_status
    summary.elapsed_seconds = time.time() - start
    return summary.to_dict()
class DiscoveryPipeline:
    """Wrapper class for discovery pipeline matching test expectations."""
    
    def __init__(self, engine, capabilities=None, limit=None, dry_run=False):
        self.engine = engine
        self.capabilities = capabilities
        self.limit = limit
        self.dry_run = dry_run
    
    def run(self, conn=None, caps=None):
        from .pipeline import build_discovery
        return build_discovery(self.engine, capabilities=self.capabilities, caps=caps, limit=self.limit, dry_run=self.dry_run)
    
    def build(self):
        from .pipeline import build_discovery
        return build_discovery(self.engine, capabilities=self.capabilities, limit=self.limit, dry_run=self.dry_run)

