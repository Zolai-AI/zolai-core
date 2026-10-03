"""Knowledge Artifact Builder (Phase 7 §36).

Builds versioned knowledge artifacts: manifest.json + JSONL exports.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


@dataclass
class ArtifactManifest:
    """Manifest for a knowledge artifact release (§17)."""
    version: str
    git_commit: str
    source_versions: dict[str, str]
    pipeline_version: str
    schema_version: str
    record_counts: dict[str, int]
    quality_metrics: dict[str, float]
    eval_results: dict[str, Any] | None
    file_hashes: dict[str, str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactManifest":
        return cls(**data)


def _get_git_commit() -> str:
    """Get current git commit SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd="/home/peter/Documents/Projects/zolai-ai/zolai-core",
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def _compute_file_hash(path: Path) -> str:
    """Compute SHA256 of a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _export_table_to_jsonl(engine: Engine, query: str, output_path: Path) -> int:
    """Export query results to JSONL file."""
    count = 0
    with engine.connect() as conn, output_path.open("w", encoding="utf-8") as f:
        result = conn.execution_options(stream_results=True).execute(text(query))
        for row in result:
            f.write(json.dumps(dict(row._mapping), ensure_ascii=False) + "\n")
            count += 1
    return count


def build_knowledge_artifact(
    engine: Engine,
    version: str,
    output_dir: str | Path,
    pipeline_version: str = "phase7",
    schema_version: str = "1.0",
    source_versions: dict[str, str] | None = None,
    eval_results: dict[str, Any] | None = None,
) -> ArtifactManifest:
    """Build a complete knowledge artifact for release (§17, §23).

    Args:
        engine: SQLAlchemy engine for canonical DB.
        version: Version tag (e.g., "2026.10.0").
        output_dir: Directory to write artifact files.
        pipeline_version: Pipeline version string.
        schema_version: Database schema version.
        source_versions: Dict of source → version.
        eval_results: Optional evaluation results.

    Returns:
        ArtifactManifest with hashes, counts, and metadata.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Building knowledge artifact v%s in %s", version, out_dir)

    # Define exports: (filename, SQL query, description)
    exports = [
        ("words.jsonl",
         """SELECT v.word, v.frequency, v.document_frequency, v.sentence_frequency,
                 v.pos_canonical, v.pos_candidates, v.pos_evidence, v.morph_features,
                 v.review_status, v.confidence, v.content_hash
           FROM vocabulary v
           WHERE v.is_deleted = 0""",
         "Vocabulary words with POS and morphology"),
        ("word_forms.jsonl",
         """SELECT normalized_form as word, surface_forms
           FROM word_observation_stats
           WHERE surface_forms IS NOT NULL AND surface_forms != '[]'""",
         "Word surface forms from observations"),
        ("morphology.jsonl",
         """SELECT h.subject, h.predicate, h.object, h.confidence, h.status,
                 h.evidence_ids, h.extras
           FROM hypotheses h
           WHERE h.kind = 'morph_relation'""",
         "Morphological relations from hypotheses"),
        ("pos_hypotheses.jsonl",
         """SELECT h.subject, h.predicate, h.confidence, h.status, h.evidence_ids, h.extras
           FROM hypotheses h
           WHERE h.kind = 'pos'""",
         "POS hypotheses from discovery"),
        ("grammar_patterns.jsonl",
         """SELECT pattern_id, pattern, description, function, examples, frequency,
                 normalized, components, sources, evidence_ids, confidence, status
           FROM grammar_patterns
           WHERE pattern_id LIKE 'disc_%' OR status IN ('SUPPORTED','VERIFIED')""",
         "Grammar patterns (discovered + verified)"),
        ("collocations.jsonl",
         """SELECT h.subject, h.predicate, h.object, h.confidence, h.status, h.evidence_ids, h.extras
           FROM hypotheses h
           WHERE h.kind = 'collocation'""",
         "Collocation hypotheses"),
        ("knowledge_claims.jsonl",
         """SELECT kc.claim_type, kc.subject, kc.predicate, kc.object, kc.confidence,
                 kc.status, kc.evidence_ids, kc.source_ids, kc.notes, kc.version,
                 kc.created_at, kc.updated_at
           FROM knowledge_claims kc
           WHERE kc.status IN ('SUPPORTED','VERIFIED','CANDIDATE')""",
         "Knowledge claims with evidence"),
        ("evidence.jsonl",
         """SELECT fe.fact_type, fe.fact_key, fe.tier, fe.source, fe.confidence,
                 fe.method, fe.extractor, fe.payload, fe.provenance_hash, fe.created_at
           FROM foundation_evidence fe""",
         "Foundation evidence"),
        ("statistics.json",
         """SELECT 'dictionary' as table_name, COUNT(*) as count FROM dictionary
           UNION ALL SELECT 'dictionary_en_zo', COUNT(*) FROM dictionary_en_zo
           UNION ALL SELECT 'bible_verses', COUNT(*) FROM bible_verses
           UNION ALL SELECT 'vocabulary', COUNT(*) FROM vocabulary
           UNION ALL SELECT 'phrases', COUNT(*) FROM phrases
           UNION ALL SELECT 'grammar_patterns', COUNT(*) FROM grammar_patterns
           UNION ALL SELECT 'word_collocations', COUNT(*) FROM word_collocations
           UNION ALL SELECT 'knowledge_claims', COUNT(*) FROM knowledge_claims
           UNION ALL SELECT 'hypotheses', COUNT(*) FROM hypotheses
           UNION ALL SELECT 'foundation_evidence', COUNT(*) FROM foundation_evidence
           UNION ALL SELECT 'kg_nodes', COUNT(*) FROM kg_nodes
           UNION ALL SELECT 'kg_edges', COUNT(*) FROM kg_edges""",
         "Aggregate statistics"),
    ]

    record_counts = {}
    file_hashes = {}
    quality_metrics = {}

    # Export each table
    for filename, query, desc in exports:
        path = out_dir / filename
        count = _export_table_to_jsonl(engine, query, path)
        record_counts[filename.replace(".jsonl", "")] = count
        file_hashes[filename] = _compute_file_hash(path)
        log.info("  %s: %d records", filename, count)

    # Quality metrics
    with engine.connect() as conn:
        # ZVS compliance
        row = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN zvs_compliance_status = 'compliant' THEN 1 ELSE 0 END) as compliant
            FROM dictionary WHERE is_deleted = 0
        """)).first()
        if row and row[0] > 0:
            quality_metrics["zvs_compliance_rate"] = round(row[1] / row[0], 4)

        # Evidence coverage
        row = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN evidence_ids != '[]' AND evidence_ids != '' THEN 1 ELSE 0 END) as with_evidence
            FROM knowledge_claims
        """)).first()
        if row and row[0] > 0:
            quality_metrics["evidence_coverage_rate"] = round(row[1] / row[0], 4)

        # Hypothesis status distribution
        rows = conn.execute(text("SELECT status, COUNT(*) FROM hypotheses GROUP BY status")).fetchall()
        quality_metrics["hypothesis_status_dist"] = {r[0]: r[1] for r in rows}

    # Build manifest
    manifest = ArtifactManifest(
        version=version,
        git_commit=_get_git_commit(),
        source_versions=source_versions or {},
        pipeline_version=pipeline_version,
        schema_version=schema_version,
        record_counts=record_counts,
        quality_metrics=quality_metrics,
        eval_results=eval_results,
        file_hashes=file_hashes,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    # Write manifest
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False))
    file_hashes["manifest.json"] = _compute_file_hash(manifest_path)
    log.info("  manifest.json written")

    # Update manifest with final hashes
    manifest.file_hashes = file_hashes
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False))

    log.info("Knowledge artifact v%s built successfully", version)
    return manifest
