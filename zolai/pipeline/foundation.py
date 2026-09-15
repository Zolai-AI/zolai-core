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
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import text as sql_text
from sqlalchemy.engine import Engine

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
    from zolai.data.repositories.foundation import (
        FoundationStagingWordsRepository,
        FoundationStagingSentencesRepository,
        FoundationStagingParagraphsRepository,
        FoundationStagingEvidenceRepository,
    )

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
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

    def __init__(
        self,
        engine: Engine | DatabaseManager,
        syllable_mode: str = "rule",
        tokenizer_model: Optional[str] = None,
    ) -> None:
        if isinstance(engine, DatabaseManager):
            self._engine = engine.engine
        else:
            self._engine = engine
        self._mgr = DatabaseManager() if not isinstance(engine, DatabaseManager) else engine
        self._repos = get_foundation_repositories(self._engine)
        self._analyzer = FoundationAnalyzer(
            syllable_mode=syllable_mode, tokenizer_model=tokenizer_model
        )
        self._batch_id: Optional[int] = None

    # -------------------------------------------------------------------------
    # Batch Management
    # -------------------------------------------------------------------------
    def _start_batch(self, batch_type: str) -> int:
        """Start a new batch job."""
        repo = self._repos["foundation_batches"]
        batch_id = repo.create_batch(batch_type)
        self._batch_id = batch_id
        return batch_id

    def _complete_batch(self, stats: PipelineStats, status: str = "completed") -> None:
        """Complete the current batch."""
        if self._batch_id:
            repo = self._repos["foundation_batches"]
            repo.update_batch_status(
                self._batch_id,
                status,
                stats={
                    "records_processed": stats.records_processed,
                    "records_staged": stats.records_staged,
                    "records_promoted": stats.records_promoted,
                    "records_queued_for_review": stats.records_queued_for_review,
                    "errors": stats.errors,
                    "duration_seconds": stats.duration_seconds,
                },
            )
            self._batch_id = None

    # -------------------------------------------------------------------------
    # Stage 1: INGEST
    # -------------------------------------------------------------------------
    def ingest_raw_corpus(
        self,
        source_type: str,
        source_path: str,
        payload: dict[str, Any],
        content_hash: Optional[str] = None,
    ) -> int:
        """Ingest raw corpus data into foundation_raw_corpus.

        Args:
            source_type: 'bible_usx' | 'web_crawl' | 'pdf' | 'dict_jsonl'
            source_path: Path to source file
            payload: Raw content as dict (will be JSON-serialized)
            content_hash: Optional pre-computed SHA256

        Returns:
            ID of created record, or 0 if duplicate (same content_hash exists).
        """
        if content_hash is None:
            content_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()

        # Check for duplicate
        existing = self._repos["foundation_raw_corpus"].get_by_content_hash(content_hash)
        if existing:
            log.info("Duplicate content_hash %s, skipping", content_hash[:8])
            return 0

        repo = self._repos["foundation_raw_corpus"]
        return repo.create(
            {
                "source_type": source_type,
                "source_path": source_path,
                "content_hash": content_hash,
                "payload": json.dumps(payload, ensure_ascii=False),
                "imported_at": datetime.utcnow().isoformat(),
            }
        )

    def ingest_raw_llm(
        self,
        model: str,
        prompt_hash: str,
        response_json: dict[str, Any],
    ) -> int:
        """Ingest raw LLM output into foundation_raw_llm."""
        repo = self._repos["foundation_raw_llm"]
        return repo.create(
            {
                "model": model,
                "prompt_hash": prompt_hash,
                "response_json": json.dumps(response_json, ensure_ascii=False),
                "created_at": datetime.utcnow().isoformat(),
            }
        )

    def ingest_from_jsonl(
        self,
        jsonl_path: Path,
        source_type: str,
        text_field: str = "text",
    ) -> PipelineStats:
        """Ingest all records from a JSONL file into raw corpus.

        Args:
            jsonl_path: Path to JSONL file
            source_type: Source type identifier
            text_field: Field name containing text content

        Returns:
            PipelineStats with counts.
        """
        stats = PipelineStats()
        start = datetime.now()

        self._start_batch("ingest")

        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                stats.records_processed += 1
                try:
                    record = json.loads(line)
                    text_content = record.get(text_field, "")
                    if not text_content:
                        stats.errors += 1
                        continue

                    payload = {"text": text_content, **record}
                    self.ingest_raw_corpus(
                        source_type=source_type,
                        source_path=str(jsonl_path),
                        payload=payload,
                    )
                    stats.records_staged += 1
                except Exception as e:
                    log.error("Failed to ingest line: %s", e)
                    stats.errors += 1

        stats.duration_seconds = (datetime.now() - start).total_seconds()
        self._complete_batch(stats)
        return stats

    # -------------------------------------------------------------------------
    # Stage 2: BUILD STAGING
    # -------------------------------------------------------------------------
    def build_staging_from_raw(self, limit: int = 0) -> PipelineStats:
        """Analyze raw corpus and build staging records.

        Args:
            limit: Max raw records to process (0 = all)

        Returns:
            PipelineStats with counts.
        """
        stats = PipelineStats()
        start = datetime.now()

        self._start_batch("build_staging")

        word_repo = self._repos["foundation_staging_words"]
        sent_repo = self._repos["foundation_staging_sentences"]
        para_repo = self._repos["foundation_staging_paragraphs"]
        evidence_repo = self._repos["foundation_staging_evidence"]

        with self._engine.connect() as conn:
            query = sql_text("SELECT id, source_type, payload FROM foundation_raw_corpus")
            if limit > 0:
                query = sql_text(f"{query} LIMIT {limit}")
            rows = conn.execute(query).fetchall()

        for row in rows:
            stats.records_processed += 1
            try:
                payload = json.loads(row.payload) if isinstance(row.payload, str) else row.payload
                source_hash = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()

                # Analyze based on content type
                if isinstance(payload.get("text"), str):
                    txt = payload["text"]
                    # Check if paragraph or sentence
                    if len(txt.split(".")) > 2 or len(txt) > 200:
                        # Paragraph
                        self._analyze_and_stage_paragraph(txt, source_hash, para_repo, evidence_repo)
                    else:
                        # Sentence
                        self._analyze_and_stage_sentence(txt, source_hash, sent_repo, evidence_repo)

                    # Also extract and stage individual words
                    words = txt.split()
                    for word in words:
                        clean_word = word.strip(".,!?;:")
                        if clean_word and len(clean_word) > 1:
                            self._analyze_and_stage_word(clean_word, source_hash, word_repo, evidence_repo)

                stats.records_staged += 1
            except Exception as e:
                log.error("Failed to build staging for raw id %s: %s", row.id, e)
                stats.errors += 1

        stats.duration_seconds = (datetime.now() - start).total_seconds()
        self._complete_batch(stats)
        return stats

    def _analyze_and_stage_word(
        self,
        word: str,
        source_hash: str,
        word_repo: FoundationStagingWordsRepository,
        evidence_repo: FoundationStagingEvidenceRepository,
    ) -> None:
        """Analyze a word and stage it with evidence."""
        analysis = self._analyzer.analyze_word(word)

        # Create evidence from analysis
        evidence_list = []

        # T1: Bible attestation
        if analysis.bible_attestations:
            evidence_list.append(
                Evidence.create(
                    fact_type="word",
                    fact_key=f"word:{word.lower()}",
                    tier=EvidenceTier.BIBLE_PARALLEL,
                    source="bible_verses",
                    confidence=0.95,
                    payload={"attestations": list(analysis.bible_attestations)},
                )
            )

        # T2: Dictionary
        if analysis.dictionary_senses:
            evidence_list.append(
                Evidence.create(
                    fact_type="word",
                    fact_key=f"word:{word.lower()}",
                    tier=EvidenceTier.DICTIONARY,
                    source="dictionary",
                    confidence=0.9,
                    payload={"senses": list(analysis.dictionary_senses)},
                )
            )

        # T3: Corpus attestation (frequency)
        if analysis.is_known_word:
            evidence_list.append(
                Evidence.create(
                    fact_type="word",
                    fact_key=f"word:{word.lower()}",
                    tier=EvidenceTier.CORPUS_ATTESTATION,
                    source="corpus",
                    confidence=0.7,
                    payload={"form": word, "known": True},
                )
            )

        # T4: Grammar patterns
        if analysis.primary_token.morphology.pos.startswith("VERB") or \
           analysis.primary_token.morphology.pos.startswith("PART"):
            evidence_list.append(
                Evidence.create(
                    fact_type="word",
                    fact_key=f"word:{word.lower()}",
                    tier=EvidenceTier.GRAMMAR_PATTERNS,
                    source="grammar_patterns",
                    confidence=0.8,
                    payload={"pos": analysis.primary_token.morphology.pos},
                )
            )

        # Stage word
        word_repo.create(
            {
                "form": analysis.word,
                "syllables": json.dumps([s.syllable for s in analysis.primary_token.syllables]),
                "pos": analysis.primary_token.pos.tag,
                "morphology": json.dumps({
                    "prefix": analysis.primary_token.morphology.prefix,
                    "root": analysis.primary_token.morphology.root,
                    "suffix": analysis.primary_token.morphology.suffix,
                    "morphemes": list(analysis.primary_token.morphology.morphemes),
                    "compound_parts": list(analysis.primary_token.morphology.compound_parts),
                    "is_compound": analysis.primary_token.morphology.is_compound,
                    "is_reduplication": analysis.primary_token.morphology.is_reduplication,
                }),
                "meanings": json.dumps([
                    {"en": s, "source": "bible"} for s in analysis.dictionary_senses
                ] + [
                    {"en": analysis.primary_token.morphology.meaning, "source": "morphology"}
                ] if analysis.primary_token.morphology.meaning else []),
                "tone_profile": json.dumps({"ambiguous": False, "notes": ""}),
                "zvs_compliant": 1 if analysis.zvs_compliant else 0,
                "frequency": 1,
                "source_hash": source_hash,
                "created_at": datetime.utcnow().isoformat(),
            }
        )

        # Stage evidence
        for ev in evidence_list:
            evidence_repo.create(
                {
                    "fact_type": ev.fact_type,
                    "fact_key": ev.fact_key,
                    "candidate_value": json.dumps({"form": word}),
                    "evidence": json.dumps({
                        "tier": ev.tier.value,
                        "source": ev.source,
                        "confidence": ev.confidence,
                        "provenance_hash": ev.provenance_hash,
                        "payload": ev.payload,
                    }),
                    "tier": ev.tier.value,
                    "confidence": ev.confidence,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    def _analyze_and_stage_sentence(
        self,
        sentence: str,
        source_hash: str,
        sent_repo: FoundationStagingSentencesRepository,
        evidence_repo: FoundationStagingEvidenceRepository,
    ) -> None:
        """Analyze a sentence and stage it with evidence."""
        analysis = self._analyzer.analyze_sentence(sentence)

        # Create evidence
        evidence_list = []

        # T1: Bible parallel
        if analysis.bible_reference:
            evidence_list.append(
                Evidence.create(
                    fact_type="sentence",
                    fact_key=f"sentence:{hashlib.sha256(sentence.encode()).hexdigest()[:12]}",
                    tier=EvidenceTier.BIBLE_PARALLEL,
                    source="bible_verses",
                    confidence=0.95,
                    payload={"reference": analysis.bible_reference},
                )
            )

        # T3: Grammar patterns
        if analysis.grammar_patterns_matched:
            evidence_list.append(
                Evidence.create(
                    fact_type="sentence",
                    fact_key=f"sentence:{hashlib.sha256(sentence.encode()).hexdigest()[:12]}",
                    tier=EvidenceTier.GRAMMAR_PATTERNS,
                    source="grammar_patterns",
                    confidence=0.8,
                    payload={"patterns": list(analysis.grammar_patterns_matched)},
                )
            )

        # T4: Corpus attestation
        evidence_list.append(
            Evidence.create(
                fact_type="sentence",
                fact_key=f"sentence:{hashlib.sha256(sentence.encode()).hexdigest()[:12]}",
                tier=EvidenceTier.CORPUS_ATTESTATION,
                source="corpus",
                confidence=0.6,
                payload={"text": sentence},
            )
        )

        # Stage sentence
        sent_repo.create(
            {
                "text": analysis.sentence,
                "tokens": json.dumps([{
                    "form": t.form,
                    "syllables": [s.syllable for s in t.syllables],
                    "pos": t.pos.tag,
                    "morphology": {
                        "root": t.morphology.root,
                        "pos": t.morphology.pos,
                    },
                } for t in analysis.tokens]),
                "pos_tags": json.dumps([p.tag for p in analysis.pos_tags]),
                "structure": json.dumps({
                    "dependencies": [],
                    "grammar_features": {
                        "tense": analysis.tense,
                        "negation": analysis.negation_type,
                        "question": analysis.question_type,
                    },
                }),
                "translation": json.dumps({"en": analysis.english_translation} if analysis.english_translation else {}),
                "grammar": json.dumps({
                    "tense": analysis.tense,
                    "negation": analysis.negation_type,
                    "question_type": analysis.question_type,
                    "sov_valid": analysis.sov_valid,
                    "ergative": analysis.ergative_present,
                }),
                "source_hash": source_hash,
                "created_at": datetime.utcnow().isoformat(),
            }
        )

        # Stage evidence
        for ev in evidence_list:
            evidence_repo.create(
                {
                    "fact_type": ev.fact_type,
                    "fact_key": ev.fact_key,
                    "candidate_value": json.dumps({"text": sentence}),
                    "evidence": json.dumps({
                        "tier": ev.tier.value,
                        "source": ev.source,
                        "confidence": ev.confidence,
                        "provenance_hash": ev.provenance_hash,
                        "payload": ev.payload,
                    }),
                    "tier": ev.tier.value,
                    "confidence": ev.confidence,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    def _analyze_and_stage_paragraph(
        self,
        paragraph: str,
        source_hash: str,
        para_repo: FoundationStagingParagraphsRepository,
        evidence_repo: FoundationStagingEvidenceRepository,
    ) -> None:
        """Analyze a paragraph and stage it with evidence."""
        analysis = self._analyzer.analyze_paragraph(paragraph)

        # Create evidence
        fact_key = f"paragraph:{hashlib.sha256(paragraph.encode()).hexdigest()[:12]}"
        evidence_list = [
            Evidence.create(
                fact_type="paragraph",
                fact_key=fact_key,
                tier=EvidenceTier.CORPUS_ATTESTATION,
                source="corpus",
                confidence=0.6,
                payload={"text": paragraph[:200]},
            )
        ]

        # Stage paragraph
        para_repo.create(
            {
                "text": analysis.paragraph,
                "sentences": json.dumps([{
                    "sentence": s.sentence,
                    "tense": s.tense,
                    "negation": s.negation_type,
                    "question": s.question_type,
                } for s in analysis.sentences]),
                "style_profile": json.dumps(analysis.style_profile),
                "source_hash": source_hash,
                "created_at": datetime.utcnow().isoformat(),
            }
        )

        # Stage evidence
        for ev in evidence_list:
            evidence_repo.create(
                {
                    "fact_type": ev.fact_type,
                    "fact_key": ev.fact_key,
                    "candidate_value": json.dumps({"text": paragraph}),
                    "evidence": json.dumps({
                        "tier": ev.tier.value,
                        "source": ev.source,
                        "confidence": ev.confidence,
                        "provenance_hash": ev.provenance_hash,
                        "payload": ev.payload,
                    }),
                    "tier": ev.tier.value,
                    "confidence": ev.confidence,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )

    # -------------------------------------------------------------------------
    # Stage 3: PROMOTE (Consensus)
    # -------------------------------------------------------------------------
    def promote_to_canonical(self, fact_type: str = "word", limit: int = 0) -> PipelineStats:
        """Run consensus on staged records and promote to canonical layer.

        Args:
            fact_type: 'word' | 'sentence' | 'paragraph'
            limit: Max records to promote (0 = all)

        Returns:
            PipelineStats with counts.
        """
        stats = PipelineStats()
        start = datetime.now()

        self._start_batch("promote")

        if fact_type == "word":
            stats = self._promote_words(limit)
        elif fact_type == "sentence":
            stats = self._promote_sentences(limit)
        elif fact_type == "paragraph":
            stats = self._promote_paragraphs(limit)
        else:
            raise ValueError(f"Unknown fact_type: {fact_type}")

        stats.duration_seconds = (datetime.now() - start).total_seconds()
        self._complete_batch(stats)
        return stats

    def _promote_words(self, limit: int) -> PipelineStats:
        stats = PipelineStats()

        staging_repo = self._repos["foundation_staging_words"]
        canonical_repo = self._repos["canonical_words"]
        evidence_repo = self._repos["foundation_evidence"]
        consensus_repo = self._repos["foundation_consensus"]
        review_repo = self._repos["foundation_review_queue"]

        # Get all unique forms from staging
        with self._engine.connect() as conn:
            query = text("SELECT DISTINCT form FROM foundation_staging_words")
            if limit > 0:
                query = text(f"{query} LIMIT {limit}")
            forms = [row[0] for row in conn.execute(query).fetchall()]

        for form in forms:
            stats.records_processed += 1
            try:
                # Get all staging records for this form
                staged = staging_repo.get_by_form(form)
                if not staged:
                    continue

                # Build candidates from staging + evidence
                candidates = self._build_word_candidates(form, staged)

                # Run consensus
                result = run_consensus(candidates, fact_type="word")

                if result.decision:
                    # Promote to canonical
                    decision = result.decision
                    canonical_repo.create(
                        {
                            "form": decision["form"],
                            "syllables": json.dumps(decision.get("syllables", [])),
                            "syllable_count": len(decision.get("syllables", [])),
                            "pos": decision.get("pos", "X"),
                            "morphology": json.dumps(decision.get("morphology", {})),
                            "meanings": json.dumps(decision.get("meanings", [])),
                            "tone_profile": json.dumps(decision.get("tone_profile", {})),
                            "zvs_compliant": 1 if decision.get("zvs_compliant", True) else 0,
                            "frequency": decision.get("frequency", 1),
                            "version": 1,
                            "source_hash": staged[0]["source_hash"],
                            "verified_at": datetime.utcnow().isoformat(),
                            "verified_by": f"consensus:{result.method}",
                            "evidence_ids": json.dumps([]),  # Would populate with actual evidence IDs
                            "created_at": datetime.utcnow().isoformat(),
                        }
                    )
                    stats.records_promoted += 1

                    # Record consensus
                    consensus_repo.create(
                        {
                            "fact_type": "word",
                            "fact_key": f"word:{form.lower()}",
                            "candidates": json.dumps([{
                                "evidence_id": 0,  # placeholder
                                "value": c.value,
                                "weight": c.aggregate_confidence(),
                            } for c in candidates]),
                            "decision": json.dumps(result.decision),
                            "confidence": result.confidence,
                            "method": result.method,
                            "threshold": result.threshold,
                            "agreeing_count": result.agreeing_candidates,
                            "notes": json.dumps(list(result.notes)),
                            "created_at": datetime.utcnow().isoformat(),
                        }
                    )

                    # If low confidence, queue for review
                    if result.confidence < 0.8:
                        review_repo.add_to_queue(
                            fact_type="word",
                            fact_key=f"word:{form.lower()}",
                            priority=int((0.8 - result.confidence) * 100),
                        )
                        stats.records_queued_for_review += 1
                else:
                    # No consensus - queue for review
                    review_repo.add_to_queue(
                        fact_type="word",
                        fact_key=f"word:{form.lower()}",
                        priority=50,
                    )
                    stats.records_queued_for_review += 1

            except Exception as e:
                log.error("Failed to promote word %s: %s", form, e)
                stats.errors += 1

        return stats

    def _build_word_candidates(self, form: str, staged: list[dict]) -> list[Candidate]:
        """Build candidates from staged records and their evidence."""
        candidates_map: dict[str, list[Evidence]] = {}

        for s in staged:
            # Get evidence for this word
            evidence_rows = self._repos["foundation_staging_evidence"].get_by_fact("word", f"word:{form.lower()}")
            for ev_row in evidence_rows:
                ev_data = json.loads(ev_row["evidence"])
                evidence = Evidence(
                    fact_type="word",
                    fact_key=f"word:{form.lower()}",
                    tier=EvidenceTier(ev_data["tier"]),
                    source=ev_data["source"],
                    confidence=ev_data["confidence"],
                    provenance_hash=ev_data["provenance_hash"],
                    payload=ev_data["payload"],
                )
                key = json.dumps(s, sort_keys=True)
                candidates_map.setdefault(key, []).append(evidence)

        candidates = []
        for value_json, ev_list in candidates_map.items():
            value = json.loads(value_json)
            candidates.append(
                Candidate(
                    fact_type="word",
                    fact_key=f"word:{form.lower()}",
                    value={
                        "form": value["form"],
                        "syllables": json.loads(value["syllables"]) if isinstance(value["syllables"], str) else value["syllables"],
                        "pos": value["pos"],
                        "morphology": json.loads(value["morphology"]) if isinstance(value["morphology"], str) else value["morphology"],
                        "meanings": json.loads(value["meanings"]) if isinstance(value["meanings"], str) else value["meanings"],
                        "tone_profile": json.loads(value["tone_profile"]) if isinstance(value["tone_profile"], str) else value["tone_profile"],
                        "zvs_compliant": value.get("zvs_compliant", 1) == 1,
                        "frequency": value.get("frequency", 1),
                    },
                    evidence=tuple(ev_list),
                    source="staging",
                )
            )

        return candidates

    def _promote_sentences(self, limit: int) -> PipelineStats:
        stats = PipelineStats()
        # Similar pattern to _promote_words but for sentences
        # Implementation left as exercise - follows same consensus pattern
        return stats

    def _promote_paragraphs(self, limit: int) -> PipelineStats:
        stats = PipelineStats()
        # Similar pattern
        return stats

    # -------------------------------------------------------------------------
    # Status / Utility
    # -------------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        """Get pipeline status across all layers."""
        return {
            "raw_corpus": self._repos["foundation_raw_corpus"].count(),
            "raw_llm": self._repos["foundation_raw_llm"].count(),
            "staging_words": self._repos["foundation_staging_words"].count(),
            "staging_sentences": self._repos["foundation_staging_sentences"].count(),
            "staging_paragraphs": self._repos["foundation_staging_paragraphs"].count(),
            "staging_evidence": self._repos["foundation_staging_evidence"].count(),
            "canonical_words": self._repos["canonical_words"].count(),
            "canonical_sentences": self._repos["canonical_sentences"].count(),
            "canonical_paragraphs": self._repos["canonical_paragraphs"].count(),
            "evidence": self._repos["foundation_evidence"].count(),
            "verifications": self._repos["foundation_verifications"].count(),
            "consensus": self._repos["foundation_consensus"].count(),
            "batches": self._repos["foundation_batches"].count(),
            "review_queue_pending": len(self._repos["foundation_review_queue"].get_pending()),
            "metrics": self._repos["foundation_metrics"].count(),
        }

    def run_full_pipeline(
        self,
        jsonl_path: Optional[Path] = None,
        source_type: str = "corpus",
        promote_types: list[str] = None,
    ) -> PipelineStats:
        """Run the complete pipeline: ingest → build_staging → promote.

        Args:
            jsonl_path: Optional JSONL file to ingest
            source_type: Source type for ingestion
            promote_types: List of fact types to promote (default: ["word"])

        Returns:
            Combined PipelineStats.
        """
        if promote_types is None:
            promote_types = ["word"]

        combined = PipelineStats()

        if jsonl_path:
            stats = self.ingest_from_jsonl(jsonl_path, source_type)
            combined.records_processed += stats.records_processed
            combined.records_staged += stats.records_staged
            combined.errors += stats.errors

        stats = self.build_staging_from_raw()
        combined.records_processed += stats.records_processed
        combined.records_staged += stats.records_staged
        combined.errors += stats.errors

        for ftype in promote_types:
            stats = self.promote_to_canonical(ftype)
            combined.records_processed += stats.records_processed
            combined.records_promoted += stats.records_promoted
            combined.records_queued_for_review += stats.records_queued_for_review
            combined.errors += stats.errors

        return combined
