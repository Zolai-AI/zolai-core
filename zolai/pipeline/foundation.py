"""Foundation ETL Pipeline — Ingest, Build Staging, Promote to Canonical.

Orchestrates FoundationAnalyzer and consensus to move data through
Raw → Staging → Canonical layers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from zolai.data.database import DatabaseManager
from zolai.data.repositories import get_foundation_repositories
from zolai.foundation import (
    Candidate,
    Evidence,
    EvidenceTier,
    FoundationAnalyzer,
    run_consensus,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@dataclass(frozen=False)
class PipelineStats:
    """Statistics from a pipeline run."""
    records_processed: int = 0
    records_staged: int = 0
    records_promoted: int = 0
    records_queued_for_review: int = 0
    errors: int = 0
    duration_seconds: float = 0.0


class FoundationETL:
    """ETL pipeline for Foundation layer.

    Stages:
    1. INGEST: Raw corpus/LLM → foundation_raw_corpus / foundation_raw_llm
    2. BUILD_STAGING: Analyze raw → foundation_staging_* + foundation_staging_evidence
    3. PROMOTE: Consensus → canonical_* + foundation_evidence / foundation_consensus
    4. VERIFY: Run verifiers → foundation_verifications
    """

    def __init__(self, db_path: Optional[str] = None, mgr: Optional[DatabaseManager] = None) -> None:
        """Initialize ETL with database connection.

        Args:
            db_path: Path to SQLite database file (or URL)
            mgr: Optional pre-initialized DatabaseManager
        """
        self.db_path = db_path
        self._mgr = mgr
        self._analyzer: Optional[FoundationAnalyzer] = None
        self._repos: Optional[dict] = None

    def _get_manager(self) -> DatabaseManager:
        if self._mgr is None:
            if self.db_path is None:
                raise ValueError("Either db_path or mgr must be provided")
            self._mgr = DatabaseManager(self.db_path)
            self._mgr.init_db()
        return self._mgr

    def _get_repos(self) -> dict:
        if self._repos is None:
            mgr = self._get_manager()
            self._repos = get_foundation_repositories(mgr.engine)
        return self._repos

    def _get_analyzer(self) -> FoundationAnalyzer:
        if self._analyzer is None:
            self._analyzer = FoundationAnalyzer()
        return self._analyzer

    def _complete_batch(self, stats: PipelineStats, status: str = "completed") -> None:
        """Log batch completion and update stats."""
        repos = self._get_repos()
        batch_repo = repos["foundation_batches"]
        batch_repo.update_batch_status(
            stats.batch_id if hasattr(stats, "batch_id") else 0,
            status,
            {
                "records_processed": stats.records_processed,
                "records_staged": stats.records_staged,
                "records_promoted": stats.records_promoted,
                "records_queued_for_review": stats.records_queued_for_review,
                "errors": stats.errors,
                "duration_seconds": stats.duration_seconds,
            },
        )

    # ── Stage 1: INGEST ─────────────────────────────────────────────────────

    def ingest_from_jsonl(
        self,
        jsonl_path: Path,
        source_type: str = "jsonl",
        batch_size: int = 1000,
    ) -> PipelineStats:
        """Ingest raw corpus from JSONL file into foundation_raw_corpus.

        Expected JSONL format: each line is a JSON object with at least "text" field.
        """
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        raw_corpus = repos["foundation_raw_corpus"]
        batch_repo = repos["foundation_batches"]

        # Create batch record
        batch_id = batch_repo.create_batch(source_type, {})
        stats.batch_id = batch_id

        try:
            with jsonl_path.open(encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                        text = record.get("text", "")
                        if not text:
                            stats.errors += 1
                            continue

                        # Compute content hash
                        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

                        # Check for duplicates
                        existing = raw_corpus.get_by_content_hash(content_hash)
                        if existing:
                            stats.errors += 1
                            continue

                        # Insert raw corpus record
                        raw_corpus.create({
                            "source_type": source_type,
                            "source_path": str(jsonl_path),
                            "content_hash": content_hash,
                            "payload": json.dumps(record, ensure_ascii=False),
                        })
                        stats.records_processed += 1

                        # Batch commit
                        if stats.records_processed % batch_size == 0:
                            log.info(f"Ingested {stats.records_processed} records...")

                    except json.JSONDecodeError:
                        stats.errors += 1
                        continue

            stats.duration_seconds = (datetime.now() - start).total_seconds()
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    def ingest_raw_corpus(
        self,
        source_type: str,
        source_path: str,
        batch_size: int = 1000,
    ) -> PipelineStats:
        """Generic ingest for various source types."""
        return self.ingest_from_jsonl(Path(source_path), source_type, batch_size)

    # ── Stage 2: BUILD_STAGING ──────────────────────────────────────────────

    def build_staging_from_raw(
        self,
        limit: int = 0,
        batch_size: int = 1000,
    ) -> PipelineStats:
        """Build staging layer from raw corpus using FoundationAnalyzer.

        Processes foundation_raw_corpus, extracts text, runs FoundationAnalyzer
        on each record, and populates staging tables with results + evidence.
        """
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        raw_corpus = repos["foundation_raw_corpus"]
        staging_words = repos["foundation_staging_words"]
        staging_sentences = repos["foundation_staging_sentences"]
        staging_paragraphs = repos["foundation_staging_paragraphs"]
        staging_evidence = repos["foundation_staging_evidence"]
        analyzer = self._get_analyzer()
        batch_repo = repos["foundation_batches"]

        batch_id = batch_repo.create_batch("build_staging", {})
        stats.batch_id = batch_id

        try:
            # Get unprocessed raw records
            raw_records = raw_corpus.get_unprocessed(limit=limit)
            if not raw_records:
                log.info("No raw records to process")
                stats.duration_seconds = (datetime.now() - start).total_seconds()
                self._complete_batch(stats, "completed")
                return stats

            for i, raw in enumerate(raw_records):
                try:
                    payload = json.loads(raw["payload"])
                    text = payload.get("text", "")
                    if not text:
                        stats.errors += 1
                        continue

                    # Analyze the text
                    word_results = analyzer.analyze_word(text) if " " not in text else None
                    sentence_results = analyzer.analyze_sentence(text)
                    paragraph_results = analyzer.analyze_paragraph(text)

                    # Build staging word (if single word)
                    if word_results:
                        source_hash = hashlib.sha256(text.encode()).hexdigest()
                        existing = staging_words.get_by_hash(source_hash)
                        if not existing:
                            tok = word_results.primary_token
                            staging_words.create({
                                "form": text,
                                "syllables": json.dumps([s.syllable for s in tok.syllables]),
                                "pos": tok.pos.tag,
                                "morphology": json.dumps({
                                    "root": tok.morphology.root,
                                    "morphemes": list(tok.morphology.morphemes),
                                    "meaning": tok.morphology.meaning,
                                }),
                                "meanings": json.dumps(
                                    [word_results.dictionary_senses[0]]
                                ) if word_results.dictionary_senses else json.dumps([]),
                                "tone_profile": json.dumps({}),
                                "zvs_compliant": word_results.zvs_compliant,
                                "frequency": 1,
                                "source_hash": source_hash,
                            })
                            stats.records_staged += 1

                    # Build staging sentence
                    source_hash = hashlib.sha256(text.encode()).hexdigest()
                    existing = staging_sentences.get_by_hash(source_hash)
                    if not existing:
                        staging_sentences.create({
                            "text": text,
                            "tokens": json.dumps([t.form for t in sentence_results.tokens]),
                            "pos_tags": json.dumps([t.tag for t in sentence_results.pos_tags]),
                            "structure": json.dumps({
                                "sov_valid": sentence_results.sov_valid,
                                "ergative_present": sentence_results.ergative_present,
                                "negation_type": sentence_results.negation_type,
                                "question_type": sentence_results.question_type,
                                "tense": sentence_results.tense,
                            }),
                            "translation": json.dumps({}),
                            "grammar": json.dumps(list(sentence_results.grammar_patterns_matched)),
                            "source_hash": source_hash,
                        })
                        stats.records_staged += 1

                    # Build staging paragraph
                    existing = staging_paragraphs.get_by_hash(source_hash)
                    if not existing:
                        staging_paragraphs.create({
                            "text": text,
                            "sentences": json.dumps([sentence_results.sentence]),
                            "style_profile": json.dumps(paragraph_results.style_profile),
                            "source_hash": source_hash,
                        })
                        stats.records_staged += 1

                    # Build staging evidence
                    staging_evidence.create({
                        "fact_type": "sentence",
                        "fact_key": f"sentence:{hashlib.sha256(text.encode()).hexdigest()[:16]}",
                        "candidate_value": json.dumps({"text": text}),
                        "evidence": json.dumps({
                            "sov_valid": sentence_results.sov_valid,
                            "ergative": sentence_results.ergative_present,
                            "zvs_compliant": sentence_results.zvs_compliant,
                        }),
                        "tier": 3,  # T3 = corpus
                        "confidence": 0.7,
                    })

                    if (i + 1) % batch_size == 0:
                        log.info(f"Staged {stats.records_staged} records...")

                except Exception as e:
                    log.error(f"Error processing record: {e}")
                    stats.errors += 1
                    continue

            stats.duration_seconds = (datetime.now() - start).total_seconds()
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    # ── Stage 3: PROMOTE ────────────────────────────────────────────────────

    def promote_staging_to_canonical(
        self,
        threshold: float = 0.9,
        batch_size: int = 1000,
    ) -> PipelineStats:
        """Promote staged records to canonical layer via consensus."""
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        staging_words = repos["foundation_staging_words"]
        staging_evidence = repos["foundation_staging_evidence"]
        canonical_words = repos["canonical_words"]
        evidence_repo = repos["foundation_evidence"]
        consensus_repo = repos["foundation_consensus"]
        batch_repo = repos["foundation_batches"]

        batch_id = batch_repo.create_batch("promote_canonical", {"threshold": threshold})
        stats.batch_id = batch_id

        try:
            # Promote words
            staged_words = staging_words.get_unpromoted(limit=batch_size)
            for sw in staged_words:
                try:
                    # Build candidate from staging
                    candidate = Candidate(
                        fact_type="word",
                        fact_key=f"word:{sw['form']}",
                        value={
                            "form": sw["form"],
                            "syllables": json.loads(sw["syllables"]),
                            "pos": sw["pos"],
                            "morphology": json.loads(sw["morphology"]),
                            "meanings": json.loads(sw["meanings"]),
                            "zvs_compliant": sw["zvs_compliant"],
                        },
                        evidence=(),
                        source="staging",
                    )

                    # Get supporting evidence
                    ev_records = staging_evidence.get_by_fact("word", f"word:{sw['form']}")
                    evidence = [
                        Evidence(
                            tier=EvidenceTier(e["tier"]),
                            source=e["source"],
                            confidence=e["confidence"],
                            provenance_hash=e.get("provenance_hash", ""),
                            payload=e["evidence"],
                        )
                        for e in ev_records
                    ]
                    candidate.evidence = tuple(evidence)

                    # Run consensus
                    consensus = run_consensus([candidate])

                    if consensus.confidence >= threshold:
                        # Promote to canonical
                        canonical_words.create({
                            "form": candidate.value["form"],
                            "syllables": json.dumps(candidate.value["syllables"]),
                            "syllable_count": len(candidate.value["syllables"]),
                            "pos": candidate.value["pos"],
                            "morphology": json.dumps(candidate.value["morphology"]),
                            "meanings": json.dumps(candidate.value["meanings"]),
                            "tone_profile": json.dumps({}),
                            "zvs_compliant": candidate.value["zvs_compliant"],
                            "frequency": 1,
                            "version": 1,
                            "source_hash": sw["source_hash"],
                            "verified_at": datetime.now().isoformat(),
                            "verified_by": f"consensus:{consensus.method}",
                            "evidence_ids": json.dumps([e["id"] for e in ev_records]),
                        })
                        stats.records_promoted += 1

                        # Store evidence
                        for e in ev_records:
                            evidence_repo.create({
                                "fact_type": "word",
                                "fact_key": f"word:{sw['form']}",
                                "tier": e["tier"],
                                "source": e["source"],
                                "confidence": e["confidence"],
                                "provenance_hash": e.get("provenance_hash", ""),
                                "payload": e["evidence"],
                            })

                        # Store consensus
                        consensus_repo.create({
                            "fact_type": "word",
                            "fact_key": f"word:{sw['form']}",
                            "candidates": json.dumps([candidate.value]),
                            "decision": json.dumps(consensus.decision),
                            "confidence": consensus.confidence,
                            "method": consensus.method,
                            "threshold": consensus.threshold,
                            "agreeing_count": consensus.agreeing_candidates,
                            "notes": json.dumps(list(consensus.notes)),
                        })
                    else:
                        # Queue for review
                        review_repo = repos["foundation_review_queue"]
                        review_repo.create({
                            "fact_type": "word",
                            "fact_key": f"word:{sw['form']}",
                            "priority": 1,
                            "assignee": "",
                        })
                        stats.records_queued_for_review += 1

                    stats.records_processed += 1

                except Exception as e:
                    log.error(f"Error promoting word {sw['form']}: {e}")
                    stats.errors += 1
                    continue

            # Similar logic for sentences and paragraphs would go here...

            stats.duration_seconds = (datetime.now() - start).total_seconds()
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    # ── Stage 4: VERIFY ─────────────────────────────────────────────────────

    def run_verification(
        self,
        limit: int = 0,
    ) -> PipelineStats:
        """Run verifiers on promoted canonical records."""
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        batch_repo = repos["foundation_batches"]

        batch_id = batch_repo.create_batch("verify", {})
        stats.batch_id = batch_id

        try:
            # Get canonical records needing verification
            # This would query for records without verification entries
            # For now, just return empty stats
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    # ── Full Pipeline ───────────────────────────────────────────────────────

    def run_full_pipeline(
        self,
        threshold: float = 0.9,
        batch_size: int = 1000,
    ) -> dict[str, PipelineStats]:
        """Run all pipeline stages in sequence."""
        log.info("Starting full Foundation ETL pipeline...")

        results = {}
        results["ingest"] = self.ingest_from_jsonl(Path("data/raw/sample.jsonl"))
        results["build_staging"] = self.build_staging_from_raw(limit=0, batch_size=batch_size)
        results["promote"] = self.promote_staging_to_canonical(threshold=threshold, batch_size=batch_size)
        results["verify"] = self.run_verification(limit=0)

        log.info("Full pipeline completed")
        return results


def get_pipeline_stats_schema() -> dict[str, str]:
    """Return schema for PipelineStats for documentation."""
    return {
        "records_processed": "int",
        "records_staged": "int",
        "records_promoted": "int",
        "records_queued_for_review": "int",
        "errors": "int",
        "duration_seconds": "float",
    }
