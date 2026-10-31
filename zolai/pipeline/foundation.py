"""Foundation ETL Pipeline — Ingest, Build Staging, Promote to Canonical.

Orchestrates FoundationAnalyzer and consensus to move data through
Raw → Staging → Canonical layers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import text

from zolai.data.database import DatabaseManager
from zolai.data.repositories import get_foundation_repositories
from zolai.foundation import (
    Candidate,
    Evidence,
    EvidenceTier,
    FoundationAnalyzer,
    run_consensus,
)
from zolai.learning.bible_pattern_learner import classify_clause, extract_structure
from zolai.shared.text import tokenize_words

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

    # ── Stage 3b: PROMOTE FROM ATTESTATIONS ────────────────────────────────

    #: ``attestation_index.source`` → evidence tier. ``extra`` is a second
    #: community dictionary, so it shares the dictionary tier.
    ATTESTATION_TIERS: dict[str, EvidenceTier] = {
        "bible": EvidenceTier.BIBLE_PARALLEL,
        "dict": EvidenceTier.DICTIONARY,
        "extra": EvidenceTier.DICTIONARY,
        "corpus": EvidenceTier.CORPUS_ATTESTATION,
    }

    def promote_words_from_attestations(
        self,
        threshold: float = 0.85,
        limit: int = 0,
    ) -> PipelineStats:
        """Promote attested words into ``canonical_words``.

        The raw-corpus staging path seeds word candidates with sentence
        evidence only, so they always reach consensus empty-handed and land in
        the review queue. ``attestation_index`` already carries the
        independent source memberships for every known word, so that is the
        candidate set here: one evidence item per distinct tier, the two-tier
        gate, then the consensus threshold. Words that clear the gate but miss
        the threshold go to the review queue.

        Provenance is recorded once per batch (``foundation_batches``) rather
        than per row, which keeps a 20k-word backfill from writing 150k audit
        entries.
        """
        start = datetime.now()
        stats = PipelineStats()

        mgr = self._get_manager()
        analyzer = self._get_analyzer()
        batch_repo = self._get_repos()["foundation_batches"]
        stats.batch_id = batch_repo.create_batch("promote_words", {"threshold": threshold})

        limit_clause = f" LIMIT {int(limit)}" if limit else ""
        query = text(
            f"""
            SELECT a.word, GROUP_CONCAT(DISTINCT a.source) AS sources,
                   v.syllables, v.syllable_count, v.pos_canonical, v.pos,
                   v.frequency, v.english, d.gloss
            FROM attestation_index a
            LEFT JOIN (
                SELECT headword, MAX(syllables) AS syllables,
                       MAX(syllable_count) AS syllable_count,
                       MAX(pos_canonical) AS pos_canonical,
                       MAX(pos) AS pos, MAX(frequency) AS frequency,
                       MAX(english) AS english
                FROM vocabulary GROUP BY headword
            ) v ON v.headword = a.word
            LEFT JOIN (
                SELECT zolai, MAX(english_clean) AS gloss
                FROM dictionary GROUP BY zolai
            ) d ON d.zolai = a.word
            WHERE a.word NOT IN (SELECT form FROM canonical_words)
              AND ('word:' || a.word) NOT IN (
                  SELECT fact_key FROM foundation_review_queue
                  WHERE fact_type = 'word'
              )
            GROUP BY a.word
            HAVING COUNT(DISTINCT a.source) >= 2
            ORDER BY COUNT(DISTINCT a.source) DESC, a.word{limit_clause}
            """
        )

        evidence_insert = text(
            """
            INSERT INTO foundation_evidence
                (fact_type, fact_key, tier, source, confidence,
                 provenance_hash, payload, created_at, method)
            VALUES ('word', :fact_key, :tier, :source, :confidence,
                    :provenance_hash, :payload, :created_at, 'attestation_index')
            """
        )
        review_insert = text(
            """
            INSERT INTO foundation_review_queue
                (fact_type, fact_key, priority, assignee, status, created_at)
            VALUES ('word', :fact_key, 2, '', 'pending', :created_at)
            """
        )
        consensus_insert = text(
            """
            INSERT INTO foundation_consensus
                (fact_type, fact_key, candidates, decision, confidence,
                 method, threshold, agreeing_count, notes, created_at)
            VALUES ('word', :fact_key, :candidates, :decision, :confidence,
                    :method, :threshold, :agreeing_count, :notes, :created_at)
            """
        )
        canonical_insert = text(
            """
            INSERT INTO canonical_words
                (form, syllables, syllable_count, pos, morphology, meanings,
                 tone_profile, zvs_compliant, frequency, version, source_hash,
                 verified_at, verified_by, evidence_ids, created_at)
            VALUES (:form, :syllables, :syllable_count, :pos, '{}', :meanings,
                    '{}', :zvs_compliant, :frequency, 1, :source_hash,
                    :verified_at, :verified_by, :evidence_ids, :created_at)
            """
        )

        now = datetime.now().isoformat()
        promoted = queued = skipped = 0

        try:
            with mgr.engine.begin() as conn:
                rows = conn.execute(query).fetchall()

                for row in rows:
                    r = row._mapping
                    form = (r["word"] or "").strip()
                    if not form or " " in form:
                        skipped += 1
                        continue

                    sources = sorted({
                        s for s in (r["sources"] or "").split(",")
                        if s in self.ATTESTATION_TIERS
                    })
                    fact_key = f"word:{form}"
                    evidence = tuple(
                        Evidence(
                            fact_type="word",
                            fact_key=fact_key,
                            tier=self.ATTESTATION_TIERS[src],
                            source=f"attestation_index:{src}",
                            confidence=self.ATTESTATION_TIERS[src].weight(),
                            provenance_hash=hashlib.sha256(
                                f"{src}:{form}".encode()
                            ).hexdigest()[:16],
                            payload={"word": form, "source": src},
                        )
                        for src in sources
                    )
                    if len({e.tier for e in evidence}) < 2:
                        skipped += 1
                        continue

                    meanings = self._merge_glosses(r["gloss"])
                    if not meanings:
                        meanings = self._merge_glosses(r["english"])
                    if not meanings:
                        skipped += 1
                        continue

                    syllables = self._parse_syllables(r["syllables"], form)
                    candidate = Candidate(
                        fact_type="word",
                        fact_key=fact_key,
                        value={
                            "form": form,
                            "syllables": syllables,
                            "pos": r["pos_canonical"] or r["pos"] or "",
                            "meanings": meanings,
                            "zvs_compliant": analyzer.check_zvs_word(form)[0],
                        },
                        evidence=evidence,
                        source="attestation_index",
                    )
                    consensus = run_consensus([candidate])

                    if consensus.confidence < threshold:
                        queued += 1
                        conn.execute(review_insert, {"fact_key": fact_key, "created_at": now})
                        continue

                    evidence_ids = []
                    for e in evidence:
                        result = conn.execute(
                            evidence_insert,
                            {
                                "fact_key": fact_key,
                                "tier": e.tier.value,
                                "source": e.source,
                                "confidence": e.confidence,
                                "provenance_hash": e.provenance_hash,
                                "payload": json.dumps(e.payload, ensure_ascii=False),
                                "created_at": now,
                            },
                        )
                        evidence_ids.append(result.lastrowid)

                    conn.execute(
                        canonical_insert,
                        {
                            "form": form,
                            "syllables": json.dumps(syllables),
                            "syllable_count": len(syllables),
                            "pos": candidate.value["pos"],
                            "meanings": json.dumps(meanings, ensure_ascii=False),
                            "zvs_compliant": 1 if candidate.value["zvs_compliant"] else 0,
                            "frequency": int(r["frequency"] or 0),
                            "source_hash": hashlib.sha256(form.encode()).hexdigest()[:16],
                            "verified_at": now,
                            "verified_by": f"consensus:{consensus.method}",
                            "evidence_ids": json.dumps(evidence_ids),
                            "created_at": now,
                        },
                    )
                    conn.execute(
                        consensus_insert,
                        {
                            "fact_key": fact_key,
                            "candidates": json.dumps([candidate.value], ensure_ascii=False),
                            "decision": json.dumps(consensus.decision, ensure_ascii=False),
                            "confidence": consensus.confidence,
                            "method": consensus.method,
                            "threshold": threshold,
                            "agreeing_count": consensus.agreeing_candidates,
                            "notes": json.dumps(list(consensus.notes)),
                            "created_at": now,
                        },
                    )
                    promoted += 1

            stats.records_processed = len(rows)
            stats.records_promoted = promoted
            stats.records_queued_for_review = queued
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            log.info(
                "promote_words_from_attestations: processed=%d promoted=%d "
                "queued=%d skipped=%d",
                len(rows), promoted, queued, skipped,
            )
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    # ── Stage 3c: PROMOTE BIBLE SENTENCES AND PARAGRAPHS ───────────────────

    #: Zolai Bible witnesses, strongest first. A verse read by two independent
    #: translations is attested twice; the second witness is the evidence that
    #: makes it canonical instead of merely collected.
    ZOLAI_BIBLE_VERSIONS: tuple[str, ...] = (
        "zo_tdb77",
        "zo_tedim2010",
        "zo_tedim1932",
        "zo_hcl06",
        "zo_fcl",
    )

    @staticmethod
    def _table_exists(conn, table: str) -> bool:
        return (
            conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).first()
            is not None
        )

    def _known_forms(self, conn) -> set[str]:
        """Every lowercase form a dictionary or the canonical layer knows."""
        forms: set[str] = set()
        for table, column in (
            ("vocabulary", "headword"),
            ("dictionary", "zolai"),
            ("canonical_words", "form"),
        ):
            if not self._table_exists(conn, table):
                continue
            for row in conn.execute(text(f"SELECT {column} FROM {table}")):
                value = str(row[0] or "").strip().lower()
                if value:
                    forms.add(value)
        return forms

    @staticmethod
    def _coverage(tokens: list[str], known: set[str]) -> float:
        if not tokens:
            return 0.0
        return sum(1 for token in tokens if token in known) / len(tokens)

    @staticmethod
    def _queue_for_review(conn, fact_type: str, fact_key: str, now: str) -> bool:
        """Record a decision the evidence gate would not promote, once.

        Returns True only when a row was inserted, so a re-run reports the
        number of *new* decisions instead of recounting pending ones.
        """
        result = conn.execute(
            text(
                """INSERT INTO foundation_review_queue
                   (fact_type, fact_key, priority, assignee, status, created_at)
                   SELECT :fact_type, :fact_key, 2, '', 'pending', :created_at
                   WHERE NOT EXISTS (
                       SELECT 1 FROM foundation_review_queue
                       WHERE fact_type = :fact_type AND fact_key = :fact_key
                   )"""
            ),
            {"fact_type": fact_type, "fact_key": fact_key, "created_at": now},
        )
        return bool(result.rowcount)

    def promote_bible_sentences(
        self,
        threshold: float = 0.9,
        limit: int = 0,
        min_coverage: float = 0.9,
    ) -> PipelineStats:
        """Promote Bible verses witnessed by two or more Zolai translations.

        The canonical value is the verse, so it is keyed by its content hash
        and stored once. Evidence is two-tier by construction: ``BIBLE_PARALLEL``
        for the second Zolai witness and ``DICTIONARY`` for the share of the
        verse's words that already carry a glossary entry.
        ``threshold_gate_consensus`` requires both tiers, so a verse with a
        single translation is never promoted — it queues for review instead.
        """
        start = datetime.now()
        stats = PipelineStats()
        mgr = self._get_manager()
        repos = self._get_repos()
        stats.batch_id = repos["foundation_batches"].create_batch(
            "promote",
            {
                "kind": "bible_sentences",
                "threshold": threshold,
                "min_coverage": min_coverage,
            },
        )
        limit_clause = f" LIMIT {int(limit)}" if limit else ""

        try:
            with mgr.engine.begin() as conn:
                if not self._table_exists(conn, "bible_verses"):
                    self._complete_batch(stats, "completed")
                    return stats
                columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(bible_verses)"))
                }
                versions = [v for v in self.ZOLAI_BIBLE_VERSIONS if v in columns]
                if len(versions) < 2:
                    self._complete_batch(stats, "completed")
                    return stats

                known = self._known_forms(conn)
                existing = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT source_hash FROM canonical_sentences")
                    )
                }
                has_english = "en_kJV" in columns
                select_sql = ", ".join(versions)
                english_sql = ", en_kJV" if has_english else ""
                rows = conn.execute(
                    text(
                        f"SELECT ref, {select_sql}{english_sql} FROM bible_verses "
                        f"WHERE ref IS NOT NULL ORDER BY ref{limit_clause}"
                    )
                ).fetchall()
                now = datetime.now().isoformat()

                for row in rows:
                    r = row._mapping
                    stats.records_processed += 1
                    texts = {
                        version: str(r[version] or "").strip() for version in versions
                    }
                    texts = {v: t for v, t in texts.items() if t}
                    if len(texts) < 2:
                        continue
                    primary_version = next(v for v in versions if v in texts)
                    primary_text = texts[primary_version]
                    source_hash = hashlib.sha256(
                        primary_text.encode()
                    ).hexdigest()[:16]
                    if source_hash in existing:
                        continue

                    fact_key = f"sentence:{source_hash}"
                    tokens = tokenize_words(primary_text)
                    coverage = round(self._coverage(tokens, known), 4)
                    if coverage < min_coverage:
                        if self._queue_for_review(
                            conn, "sentence", f"sentence:{r['ref']}", now
                        ):
                            stats.records_queued_for_review += 1
                        continue
                    witnesses = {
                        version: hashlib.sha256(text_value.encode()).hexdigest()[:16]
                        for version, text_value in texts.items()
                        if version != primary_version
                    }
                    evidence = (
                        Evidence(
                            fact_type="sentence",
                            fact_key=fact_key,
                            tier=EvidenceTier.BIBLE_PARALLEL,
                            source=f"bible:{primary_version}",
                            confidence=1.0,
                            provenance_hash=hashlib.sha256(
                                json.dumps(
                                    {
                                        "ref": r["ref"],
                                        "text": primary_text,
                                        "witnesses": witnesses,
                                    },
                                    sort_keys=True,
                                ).encode()
                            ).hexdigest()[:16],
                            payload={
                                "ref": r["ref"],
                                "version": primary_version,
                                "witness_versions": sorted(witnesses),
                                "witness_hashes": witnesses,
                            },
                        ),
                        Evidence(
                            fact_type="sentence",
                            fact_key=fact_key,
                            tier=EvidenceTier.DICTIONARY,
                            source="vocabulary+dictionary",
                            confidence=coverage,
                            provenance_hash=hashlib.sha256(
                                f"{source_hash}:{coverage}".encode()
                            ).hexdigest()[:16],
                            payload={
                                "tokens": len(tokens),
                                "known": round(coverage * len(tokens)),
                                "coverage": coverage,
                            },
                        ),
                    )
                    candidate = Candidate(
                        fact_type="sentence",
                        fact_key=fact_key,
                        value={
                            "text": primary_text,
                            "ref": r["ref"],
                            "translation": (
                                str(r["en_kJV"] or "").strip() if has_english else ""
                            ),
                            "coverage": coverage,
                        },
                        evidence=evidence,
                        source="bible_parallel",
                    )
                    consensus = run_consensus([candidate])
                    if consensus.confidence < threshold:
                        if self._queue_for_review(
                            conn, "sentence", f"sentence:{r['ref']}", now
                        ):
                            stats.records_queued_for_review += 1
                        continue

                    evidence_ids: list[int] = []
                    for e in evidence:
                        result = conn.execute(
                            text(
                                """INSERT INTO foundation_evidence
                                   (fact_type, fact_key, tier, source, confidence,
                                    provenance_hash, payload, created_at, method,
                                    document_id, sentence_id)
                                   VALUES ('sentence', :fact_key, :tier, :source,
                                           :confidence, :provenance_hash, :payload,
                                           :created_at, 'bible_parallel',
                                           :document_id, :sentence_id)"""
                            ),
                            {
                                "fact_key": fact_key,
                                "tier": e.tier.value,
                                "source": e.source,
                                "confidence": e.confidence,
                                "provenance_hash": e.provenance_hash,
                                "payload": json.dumps(
                                    e.payload, ensure_ascii=False
                                ),
                                "created_at": now,
                                "document_id": r["ref"],
                                "sentence_id": source_hash,
                            },
                        )
                        evidence_ids.append(result.lastrowid)

                    conn.execute(
                        text(
                            """INSERT INTO canonical_sentences
                               (text, tokens, pos_tags, translation, grammar,
                                version, source_hash, verified_at, verified_by,
                                evidence_ids, created_at)
                               VALUES (:text, :tokens, '[]', :translation,
                                       :grammar, 1, :source_hash, :verified_at,
                                       :verified_by, :evidence_ids, :created_at)"""
                        ),
                        {
                            "text": primary_text,
                            "tokens": json.dumps(tokens, ensure_ascii=False),
                            "translation": candidate.value["translation"],
                            "grammar": json.dumps(
                                {
                                    "ref": r["ref"],
                                    "clause": classify_clause(primary_text),
                                    "structure": extract_structure(primary_text),
                                },
                                ensure_ascii=False,
                            ),
                            "source_hash": source_hash,
                            "verified_at": now,
                            "verified_by": f"consensus:{consensus.method}",
                            "evidence_ids": json.dumps(evidence_ids),
                            "created_at": now,
                        },
                    )
                    conn.execute(
                        text(
                            """INSERT OR REPLACE INTO foundation_consensus
                               (fact_type, fact_key, candidates, decision,
                                confidence, method, threshold, agreeing_count,
                                notes, created_at)
                               VALUES ('sentence', :fact_key, :candidates,
                                       :decision, :confidence, :method,
                                       :threshold, :agreeing_count, :notes,
                                       :created_at)"""
                        ),
                        {
                            "fact_key": fact_key,
                            "candidates": json.dumps(
                                [candidate.value], ensure_ascii=False
                            ),
                            "decision": json.dumps(
                                consensus.decision, ensure_ascii=False
                            ),
                            "confidence": consensus.confidence,
                            "method": consensus.method,
                            "threshold": threshold,
                            "agreeing_count": consensus.agreeing_candidates,
                            "notes": json.dumps(list(consensus.notes)),
                            "created_at": now,
                        },
                    )
                    existing.add(source_hash)
                    stats.records_promoted += 1

            stats.duration_seconds = (datetime.now() - start).total_seconds()
            log.info(
                "promote_bible_sentences: processed=%d promoted=%d queued=%d",
                stats.records_processed,
                stats.records_promoted,
                stats.records_queued_for_review,
            )
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    def promote_bible_paragraphs(
        self,
        threshold: float = 0.9,
        limit: int = 0,
        min_coverage: float = 0.9,
    ) -> PipelineStats:
        """Promote learned Bible paragraphs whose every verse has a witness.

        Paragraph text is re-derived with :func:`learn_paragraph_texts`, the
        same loader/IDF/segmentation that wrote the layer row, so a promoted
        paragraph is exactly the unit the learner studied. Evidence is again
        two-tier: ``BIBLE_PARALLEL`` for the share of member verses read by a
        second translation and ``DICTIONARY`` for glossary coverage. A
        paragraph that only one translation attests stays out of the canonical
        layer.
        """
        from zolai.learning.hierarchy import learn_paragraph_texts

        start = datetime.now()
        stats = PipelineStats()
        mgr = self._get_manager()
        repos = self._get_repos()
        stats.batch_id = repos["foundation_batches"].create_batch(
            "promote",
            {
                "kind": "bible_paragraphs",
                "threshold": threshold,
                "min_coverage": min_coverage,
            },
        )

        texts = learn_paragraph_texts(mgr.engine, "bible")

        try:
            with mgr.engine.begin() as conn:
                if not self._table_exists(conn, "bible_verses") or not texts:
                    self._complete_batch(stats, "completed")
                    return stats
                columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(bible_verses)"))
                }
                versions = [v for v in self.ZOLAI_BIBLE_VERSIONS if v in columns]
                if len(versions) < 2:
                    self._complete_batch(stats, "completed")
                    return stats

                witness_count: dict[str, int] = {}
                select_sql = ", ".join(versions)
                for row in conn.execute(
                    text(f"SELECT ref, {select_sql} FROM bible_verses")
                ):
                    witness_count[str(row[0])] = sum(
                        1 for value in row[1:] if str(value or "").strip()
                    )

                known = self._known_forms(conn)
                existing = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT source_hash FROM canonical_paragraphs")
                    )
                }
                now = datetime.now().isoformat()
                considered = 0

                for unit_id, (first_ref, text_value, line_refs) in texts.items():
                    if limit and considered >= limit:
                        break
                    considered += 1
                    stats.records_processed += 1
                    witnessed = sum(
                        1 for ref in line_refs if witness_count.get(ref, 0) >= 2
                    )
                    parallel_share = round(witnessed / max(1, len(line_refs)), 4)
                    tokens = tokenize_words(text_value)
                    coverage = round(self._coverage(tokens, known), 4)
                    fact_key_guess = hashlib.sha256(text_value.encode()).hexdigest()[
                        :16
                    ]
                    if fact_key_guess in existing:
                        continue
                    if coverage < min_coverage:
                        if self._queue_for_review(
                            conn, "paragraph", f"paragraph:{unit_id}", now
                        ):
                            stats.records_queued_for_review += 1
                        continue
                    fact_key = f"paragraph:{fact_key_guess}"
                    sentences = [
                        part.strip()
                        for part in re.split(r"(?<=[.!?])\s+", text_value)
                        if part.strip()
                    ] or [text_value]
                    clause_types = Counter(
                        classify_clause(sentence) for sentence in sentences
                    )
                    evidence = (
                        Evidence(
                            fact_type="paragraph",
                            fact_key=fact_key,
                            tier=EvidenceTier.BIBLE_PARALLEL,
                            source="bible:parallel_versions",
                            confidence=parallel_share,
                            provenance_hash=hashlib.sha256(
                                f"{unit_id}:{parallel_share}:{len(line_refs)}".encode()
                            ).hexdigest()[:16],
                            payload={
                                "unit_id": unit_id,
                                "refs": list(line_refs[:50]),
                                "lines": len(line_refs),
                                "witnessed": witnessed,
                            },
                        ),
                        Evidence(
                            fact_type="paragraph",
                            fact_key=fact_key,
                            tier=EvidenceTier.DICTIONARY,
                            source="vocabulary+dictionary",
                            confidence=coverage,
                            provenance_hash=hashlib.sha256(
                                f"{fact_key_guess}:{coverage}".encode()
                            ).hexdigest()[:16],
                            payload={
                                "tokens": len(tokens),
                                "known": round(coverage * len(tokens)),
                                "coverage": coverage,
                            },
                        ),
                    )
                    candidate = Candidate(
                        fact_type="paragraph",
                        fact_key=fact_key,
                        # every value must be hashable: majority_vote keys on
                        # the sorted value tuple
                        value={"text": text_value, "first_ref": first_ref},
                        evidence=evidence,
                        source="bible_parallel",
                    )
                    consensus = run_consensus([candidate])
                    if consensus.confidence < threshold:
                        if self._queue_for_review(
                            conn, "paragraph", f"paragraph:{unit_id}", now
                        ):
                            stats.records_queued_for_review += 1
                        continue

                    evidence_ids: list[int] = []
                    for e in evidence:
                        result = conn.execute(
                            text(
                                """INSERT INTO foundation_evidence
                                   (fact_type, fact_key, tier, source, confidence,
                                    provenance_hash, payload, created_at, method,
                                    document_id, sentence_id)
                                   VALUES ('paragraph', :fact_key, :tier, :source,
                                           :confidence, :provenance_hash, :payload,
                                           :created_at, 'bible_parallel',
                                           :document_id, :sentence_id)"""
                            ),
                            {
                                "fact_key": fact_key,
                                "tier": e.tier.value,
                                "source": e.source,
                                "confidence": e.confidence,
                                "provenance_hash": e.provenance_hash,
                                "payload": json.dumps(
                                    e.payload, ensure_ascii=False
                                ),
                                "created_at": now,
                                "document_id": unit_id.split("|", 1)[0],
                                "sentence_id": unit_id,
                            },
                        )
                        evidence_ids.append(result.lastrowid)

                    conn.execute(
                        text(
                            """INSERT INTO canonical_paragraphs
                               (text, sentences, style_profile, version,
                                source_hash, verified_at, verified_by,
                                evidence_ids, created_at)
                               VALUES (:text, :sentences, :style_profile, 1,
                                       :source_hash, :verified_at, :verified_by,
                                       :evidence_ids, :created_at)"""
                        ),
                        {
                            "text": text_value,
                            "sentences": json.dumps(sentences, ensure_ascii=False),
                            "style_profile": json.dumps(
                                {
                                    "lines": len(line_refs),
                                    "sentences": len(sentences),
                                    "tokens": len(tokens),
                                    "unique_tokens": len(set(tokens)),
                                    "avg_line_tokens": round(
                                        len(tokens) / max(1, len(line_refs)), 2
                                    ),
                                    "clause_types": dict(clause_types.most_common()),
                                    "initial_token": tokens[0] if tokens else "",
                                    "final_token": tokens[-1] if tokens else "",
                                    "first_ref": first_ref,
                                },
                                ensure_ascii=False,
                            ),
                            "source_hash": fact_key_guess,
                            "verified_at": now,
                            "verified_by": f"consensus:{consensus.method}",
                            "evidence_ids": json.dumps(evidence_ids),
                            "created_at": now,
                        },
                    )
                    conn.execute(
                        text(
                            """INSERT OR REPLACE INTO foundation_consensus
                               (fact_type, fact_key, candidates, decision,
                                confidence, method, threshold, agreeing_count,
                                notes, created_at)
                               VALUES ('paragraph', :fact_key, :candidates,
                                       :decision, :confidence, :method,
                                       :threshold, :agreeing_count, :notes,
                                       :created_at)"""
                        ),
                        {
                            "fact_key": fact_key,
                            "candidates": json.dumps(
                                [candidate.value], ensure_ascii=False
                            ),
                            "decision": json.dumps(
                                consensus.decision, ensure_ascii=False
                            ),
                            "confidence": consensus.confidence,
                            "method": consensus.method,
                            "threshold": threshold,
                            "agreeing_count": consensus.agreeing_candidates,
                            "notes": json.dumps(list(consensus.notes)),
                            "created_at": now,
                        },
                    )
                    existing.add(fact_key_guess)
                    stats.records_promoted += 1

            stats.duration_seconds = (datetime.now() - start).total_seconds()
            log.info(
                "promote_bible_paragraphs: processed=%d promoted=%d queued=%d",
                stats.records_processed,
                stats.records_promoted,
                stats.records_queued_for_review,
            )
            self._complete_batch(stats, "completed")
            return stats

        except Exception:
            stats.duration_seconds = (datetime.now() - start).total_seconds()
            stats.errors += 1
            self._complete_batch(stats, "failed")
            raise

    @staticmethod
    def _merge_glosses(*values: Optional[str]) -> list[str]:
        """Keep the first clean gloss per source, dropping corrupt values."""
        merged: list[str] = []
        for value in values:
            gloss = (value or "").strip()
            if len(gloss) < 2 or not any(ch.isalpha() for ch in gloss):
                continue
            if gloss not in merged:
                merged.append(gloss)
        return merged

    @staticmethod
    def _parse_syllables(raw: Optional[str], form: str) -> list[str]:
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list) and parsed:
                return [str(s) for s in parsed]
        return [form]

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
