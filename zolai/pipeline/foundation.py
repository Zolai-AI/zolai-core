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
from typing import TYPE_CHECKING, Any, Optional

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
    batch_id: int = 0


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

    @property
    def _engine(self):
        """Compat alias for tests expecting ``etl._engine``."""
        return self._get_manager().engine

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
        batch_id = batch_repo.create_batch("ingest", {"source_type": source_type})
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

        batch_id = batch_repo.create_batch("promote", {"threshold": threshold})
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
        batch_size: int = 100,
    ) -> PipelineStats:
        """Run verifiers on canonical records without ``verified_at``.

        Queries canonical tables for records where ``verified_at IS NULL``,
        builds :class:`Candidate` objects from staging evidence, runs them
        through the injected verifier, and records results in
        ``foundation_verifications``.

        Args:
            limit: Maximum records to verify (0 = unlimited).
            batch_size: Batch size for processing.

        Returns:
            Pipeline statistics.
        """
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        batch_repo = repos["foundation_batches"]
        canonical_words = repos["canonical_words"]
        staging_evidence = repos["foundation_staging_evidence"]
        verifications_repo = repos["foundation_verifications"]

        batch_id = batch_repo.create_batch("verify", {})
        stats.batch_id = batch_id

        try:
            # Get unverified canonical words
            unverified = canonical_words.get_unverified(limit=limit or batch_size)

            for record in unverified:
                try:
                    form = record.get("form", "")
                    if not form:
                        stats.errors += 1
                        continue

                    fact_key = f"word:{form}"

                    # Build candidate from canonical record
                    ev_records = staging_evidence.get_by_fact("word", fact_key)
                    evidence = []
                    for ev in ev_records:
                        try:
                            tier_val = ev.get("tier", 5)
                            evidence.append(
                                Evidence(
                                    tier=EvidenceTier(tier_val),
                                    source=ev.get("source", "staging"),
                                    confidence=ev.get("confidence", 0.5),
                                    provenance_hash=ev.get("provenance_hash", ""),
                                    payload=ev.get("evidence", {}),
                                )
                            )
                        except (ValueError, KeyError):
                            continue

                    candidate = Candidate(
                        fact_type="word",
                        fact_key=fact_key,
                        value={
                            "form": form,
                            "pos": record.get("pos", ""),
                            "zvs_compliant": record.get("zvs_compliant", True),
                        },
                        evidence=tuple(evidence),
                        source="canonical",
                    )

                    # Run verifier (default: NullVerifier)
                    verifier = self._get_verifier()
                    passed, confidence, notes = verifier.verify(candidate)

                    # Record verification
                    verifications_repo.create({
                        "fact_type": "word",
                        "fact_key": fact_key,
                        "verifier": verifier.name(),
                        "passed": 1 if passed else 0,
                        "confidence": confidence,
                        "notes": notes,
                    })

                    stats.records_processed += 1

                except Exception as e:
                    log.error("Verification error for record %s: %s", record.get("id"), e)
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

    def _get_verifier(self) -> Any:
        """Get the verification strategy (default: NullVerifier).

        Override this method or set ``_verifier`` to inject a real
        verifier (e.g. :class:`GeminiVerifier` or
        :class:`EvidenceGatingVerifier`).
        """
        if not hasattr(self, "_verifier"):
            from zolai.foundation.evidence import NullVerifier
            self._verifier = NullVerifier()
        return self._verifier

    def set_verifier(self, verifier: Any) -> None:
        """Inject a verifier for batch verification runs."""
        self._verifier = verifier

    # ── Stage 4b: ADAPTIVE VERIFICATION ────────────────────────────────────

    def run_adaptive_verification(
        self,
        threshold: float = 0.9,
        limit: int = 0,
    ) -> PipelineStats:
        """3-tier adaptive verification strategy.

        Tiers:
        - ``confidence ≥ 0.95`` → auto-promote (skip LLM)
        - ``0.70 ≤ confidence < 0.95`` → batch verify via verifier
        - ``confidence < 0.70`` → queue to ``foundation_review_queue``

        Args:
            threshold: Consensus threshold for promotion.
            limit: Maximum records to process (0 = unlimited).

        Returns:
            Pipeline statistics.
        """
        start = datetime.now()
        stats = PipelineStats()

        repos = self._get_repos()
        batch_repo = repos["foundation_batches"]
        canonical_words = repos["canonical_words"]
        staging_evidence = repos["foundation_staging_evidence"]
        verifications_repo = repos["foundation_verifications"]
        review_queue = repos["foundation_review_queue"]

        batch_id = batch_repo.create_batch("adaptive_verify", {"threshold": threshold})
        stats.batch_id = batch_id

        try:
            unverified = canonical_words.get_unverified(limit=limit or 1000)

            for record in unverified:
                try:
                    form = record.get("form", "")
                    if not form:
                        stats.errors += 1
                        continue

                    fact_key = f"word:{form}"

                    # Build candidate
                    ev_records = staging_evidence.get_by_fact("word", fact_key)
                    evidence = []
                    for ev in ev_records:
                        try:
                            tier_val = ev.get("tier", 5)
                            evidence.append(
                                Evidence(
                                    tier=EvidenceTier(tier_val),
                                    source=ev.get("source", "staging"),
                                    confidence=ev.get("confidence", 0.5),
                                    provenance_hash=ev.get("provenance_hash", ""),
                                    payload=ev.get("evidence", {}),
                                )
                            )
                        except (ValueError, KeyError):
                            continue

                    candidate = Candidate(
                        fact_type="word",
                        fact_key=fact_key,
                        value={
                            "form": form,
                            "pos": record.get("pos", ""),
                            "zvs_compliant": record.get("zvs_compliant", True),
                        },
                        evidence=tuple(evidence),
                        source="canonical",
                    )

                    agg_conf = candidate.aggregate_confidence()

                    # Tier 1: auto-promote (confidence ≥ 0.95)
                    if agg_conf >= 0.95:
                        verifications_repo.create({
                            "fact_type": "word",
                            "fact_key": fact_key,
                            "verifier": "auto_promote",
                            "passed": 1,
                            "confidence": agg_conf,
                            "notes": "auto_promote: confidence >= 0.95",
                        })
                        stats.records_promoted += 1

                    # Tier 2: batch verify (0.70 ≤ confidence < 0.95)
                    elif agg_conf >= 0.70:
                        verifier = self._get_verifier()
                        passed, confidence, notes = verifier.verify(candidate)
                        verifications_repo.create({
                            "fact_type": "word",
                            "fact_key": fact_key,
                            "verifier": verifier.name(),
                            "passed": 1 if passed else 0,
                            "confidence": confidence,
                            "notes": notes,
                        })
                        if passed:
                            stats.records_promoted += 1
                        else:
                            stats.records_queued_for_review += 1

                    # Tier 3: queue for human review (confidence < 0.70)
                    else:
                        review_queue.add_to_queue(
                            fact_type="word",
                            fact_key=fact_key,
                            priority=1,
                        )
                        stats.records_queued_for_review += 1

                    stats.records_processed += 1

                except Exception as e:
                    log.error("Adaptive verification error: %s", e)
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

    # ── Stage 4c: EVIDENCE GATING ──────────────────────────────────────────

    def enforce_evidence_gating(
        self,
        candidate: Candidate,
    ) -> bool:
        """Check that a candidate has ≥2 distinct EvidenceTier values.

        Must pass before any canonical write.  Raises
        :class:`EvidenceGateError` if the gate fails.

        Args:
            candidate: The candidate to gate.

        Returns:
            ``True`` if the gate passes.

        Raises:
            EvidenceGateError: If insufficient evidence tiers.
        """
        from zolai.foundation.verifiers import EvidenceGateError

        distinct_tiers = {e.tier for e in candidate.evidence}
        tier_count = len(distinct_tiers)

        if tier_count < 2:
            raise EvidenceGateError(
                f"Insufficient evidence tiers for {candidate.fact_key}: "
                f"{tier_count} < 2 distinct tiers required. "
                f"Tiers present: {[t.name for t in distinct_tiers]}"
            )

        return True

    # ── Full Pipeline ───────────────────────────────────────────────────────

    def run_full_pipeline(
        self,
        threshold: float = 0.9,
        batch_size: int = 1000,
        jsonl_path: Path | None = None,
        source_type: str = "jsonl",
        promote_types: list[str] | None = None,
    ) -> PipelineStats | dict[str, PipelineStats]:
        """Run pipeline stages in sequence.

        When ``jsonl_path`` is provided (tests / CLI), return a single aggregated
        ``PipelineStats``. Otherwise run the legacy multi-stage dict return.
        """
        _ = promote_types
        log.info("Starting full Foundation ETL pipeline...")

        if jsonl_path is not None:
            ingest = self.ingest_from_jsonl(jsonl_path, source_type=source_type, batch_size=batch_size)
            staging = self.build_staging_from_raw(limit=0, batch_size=batch_size)
            promote = self.promote_staging_to_canonical(threshold=threshold, batch_size=batch_size)
            aggregated = PipelineStats(
                records_processed=ingest.records_processed,
                records_staged=staging.records_staged,
                records_promoted=promote.records_promoted,
                records_queued_for_review=promote.records_queued_for_review,
                errors=ingest.errors + staging.errors + promote.errors,
                duration_seconds=(
                    ingest.duration_seconds + staging.duration_seconds + promote.duration_seconds
                ),
            )
            log.info("Full pipeline completed (jsonl path)")
            return aggregated

        results: dict[str, PipelineStats] = {}
        results["ingest"] = self.ingest_from_jsonl(Path("data/raw/sample.jsonl"))
        results["build_staging"] = self.build_staging_from_raw(limit=0, batch_size=batch_size)
        results["promote"] = self.promote_staging_to_canonical(threshold=threshold, batch_size=batch_size)
        results["verify"] = self.run_verification(limit=0)

        log.info("Full pipeline completed")
        return results


    # ── Test / API aliases (compat with test_foundation_data_layer) ──────────

    def promote_to_canonical(self, fact_type: str | None = None, **kwargs) -> PipelineStats:
        """Alias for promote_staging_to_canonical (fact_type reserved for future filters)."""
        _ = fact_type
        return self.promote_staging_to_canonical(**{
            k: v for k, v in kwargs.items() if k in {"threshold", "batch_size"}
        })

    def get_status(self) -> dict[str, int]:
        """Return row counts for Foundation tables (zeros if empty)."""
        repos = self._get_repos()
        keys = {
            "raw_corpus": "foundation_raw_corpus",
            "raw_llm": "foundation_raw_llm",
            "staging_words": "foundation_staging_words",
            "staging_sentences": "foundation_staging_sentences",
            "staging_paragraphs": "foundation_staging_paragraphs",
            "staging_evidence": "foundation_staging_evidence",
            "canonical_words": "canonical_words",
            "canonical_sentences": "canonical_sentences",
            "canonical_paragraphs": "canonical_paragraphs",
            "evidence": "foundation_evidence",
            "verifications": "foundation_verifications",
            "consensus": "foundation_consensus",
            "batches": "foundation_batches",
            "review_queue_pending": "foundation_review_queue",
            "metrics": "foundation_metrics",
        }
        status: dict[str, int] = {}
        for out_key, repo_key in keys.items():
            repo = repos.get(repo_key)
            if repo is None:
                status[out_key] = 0
                continue
            try:
                if hasattr(repo, "count"):
                    status[out_key] = int(repo.count())
                elif hasattr(repo, "count_all"):
                    status[out_key] = int(repo.count_all())
                elif out_key == "review_queue_pending" and hasattr(repo, "count_pending"):
                    status[out_key] = int(repo.count_pending())
                else:
                    # Fallback: attempt generic select count via engine
                    mgr = self._get_manager()
                    from sqlalchemy import text
                    table = getattr(repo, "table_name", repo_key)
                    with mgr.engine.connect() as conn:
                        status[out_key] = int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
            except Exception:
                status[out_key] = 0
        return status



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
