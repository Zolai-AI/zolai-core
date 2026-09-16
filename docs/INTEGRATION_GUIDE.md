# Foundation Engine Integration Guide

This guide explains how to integrate and use the Foundation Intelligence Engine in zolai-core.

## Overview

The Foundation Intelligence Engine is the core data processing pipeline that transforms raw linguistic data into verified, canonical knowledge. It follows a strict RAW → Staging → Canonical flow with evidence gating and human review.

## Architecture

```
Raw Sources → ETL Pipeline → Staging → Evidence → Consensus → Canonical
                                                           ↓
                                                    Review UI (human)
                                                           ↓
                                                    Serving Layer (API)
```

## Quick Start

### 1. Installation

```bash
# Install zolai-core
pip install -e ".[dev]"

# Or with all dependencies
pip install -e ".[full]"
```

### 2. Environment Setup

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your settings
# Required:
# - ZOLAI_PG_URL (or leave empty for SQLite)
# - GEMINI_API_KEY (for LLM verification)

# Optional:
# - FOUNDATION_BATCH_SIZE=1000
# - FOUNDATION_CONCURRENCY=4
# - FOUNDATION_AUTO_THRESHOLD=0.95
# - FOUNDATION_REVIEW_THRESHOLD=0.70
```

### 3. Database Migration

```bash
# Run migrations to create foundation tables
python -m zolai.data.migrations
```

## Using the Foundation Engine

### CLI Usage

```bash
# Analyze a word
zolai foundation analyze word pasian

# Analyze a sentence
zolai foundation analyze sentence "Pasian in vantung a piangsak hi."

# Analyze a paragraph
zolai foundation analyze paragraph "Pasian in gam a piangsak hi. Vantung leh leitung a nei hi."

# Run gold evaluation
zolai foundation gold-eval

# Run ETL pipeline
zolai foundation etl --batch-size 1000

# Run verification
zolai foundation verify --batch-size 100 --concurrency 4

# View review queue
zolai foundation review list

# Approve a candidate
zolai foundation review approve <candidate_id>

# Reject a candidate
zolai foundation review reject <candidate_id> --reason "ZVS non-compliant"
```

### Python API

```python
from zolai.foundation import FoundationAnalyzer
from zolai.foundation.etl import ETLPipeline
from zolai.foundation.verification_runner import VerificationRunner
from zolai.foundation.evidence import EvidenceCollector

# 1. Analyze text
analyzer = FoundationAnalyzer()

# Word analysis
word_result = analyzer.analyze_word("pasian")
print(f"Word: {word_result.form}")
print(f"Syllables: {word_result.syllables}")
print(f"POS: {word_result.pos}")
print(f"Morphology: {word_result.morphology}")

# Sentence analysis
sentence_result = analyzer.analyze_sentence("Pasian in vantung a piangsak hi.")
print(f"Tokens: {sentence_result.tokens}")
print(f"POS tags: {sentence_result.pos_tags}")
print(f"Dependencies: {sentence_result.dependencies}")

# 2. Run ETL pipeline
pipeline = ETLPipeline(batch_size=1000)

# Process specific source
pipeline.process_source("bible", source_path="/path/to/bible.jsonl")

# Run full pipeline
pipeline.run_full_pipeline()

# 3. Collect evidence
evidence_collector = EvidenceCollector()

# Collect evidence for a word
evidence = evidence_collector.collect_word_evidence("pasian")
print(f"Evidence sources: {len(evidence)}")
for e in evidence:
    print(f"  - {e.source} (tier {e.tier}, confidence {e.confidence})")

# 4. Run verification
runner = VerificationRunner(batch_size=100, concurrency=4)

# Run batch verification
stats = runner.run_batch_verification()
print(f"Verified: {stats['verified']}")
print(f"Promoted: {stats['promoted']}")
print(f"Sent to review: {stats['sent_to_review']}")
```

## Integration Points

### 1. API Integration

The Foundation Engine is integrated into the FastAPI server:

```python
from zolai.api.server import app

# Foundation endpoints are automatically available:
# POST /foundation/analyze - Analyze text
# GET /foundation/consensus - Get consensus for fact
# GET /foundation/evidence - List evidence
# POST /foundation/batch - Trigger batch verification
# GET /review/queue - Human review queue
# POST /review/approve - Approve candidate
# POST /review/reject - Reject candidate
```

### 2. Database Integration

The Foundation Engine uses the existing database layer:

```python
from zolai.data import DatabaseManager
from zolai.data.repositories.foundation import FoundationRepository

db = DatabaseManager()
foundation_repo = FoundationRepository(db)

# Get canonical word
canonical_word = foundation_repo.get_canonical_word("pasian")

# Get evidence for a fact
evidence = foundation_repo.get_evidence("word", "word:pasian")

# Get consensus for a fact
consensus = foundation_repo.get_consensus("word", "word:pasian")
```

### 3. CLI Integration

The Foundation CLI is registered as a subcommand:

```python
# In zolai/cli/main.py
from zolai.foundation.cli import foundation_app

main_app.add_typer(foundation_app, name="foundation")
```

## ETL Pipeline

### Raw Layer

Raw data is ingested from various sources:

```python
from zolai.foundation.etl import RawIngester

ingester = RawIngester()

# Ingest Bible data
ingester.ingest_bible("/path/to/bible.jsonl")

# Ingest dictionary data
ingester.ingest_dictionary("/path/to/dictionary.jsonl")

# Ingest web corpus
ingester.ingest_corpus("/path/to/corpus.jsonl")
```

### Staging Layer

Raw data is processed into staging tables:

```python
from zolai.foundation.etl import StagingBuilder

builder = StagingBuilder()

# Build staging words from raw
builder.build_words()

# Build staging sentences from raw
builder.build_sentences()

# Build staging paragraphs from raw
builder.build_paragraphs()
```

### Canonical Layer

Staging data is promoted to canonical after verification:

```python
from zolai.foundation.etl import CanonicalPromoter

promoter = CanonicalPromoter()

# Promote verified staging words
promoter.promote_words()

# Promote verified staging sentences
promoter.promote_sentences()
```

## Verification Loop

### Adaptive Threshold

The verification loop uses adaptive thresholds:

```python
# Auto-promote: confidence >= 0.95
# Batch verify: 0.70 <= confidence < 0.95
# Human review: confidence < 0.70

runner = VerificationRunner(
    auto_threshold=0.95,
    review_threshold=0.70
)
```

### Evidence Gating

No LLM output reaches canonical without ≥2 independent sources:

```python
# Evidence tiers:
# T1: Bible (weight 1.0)
# T2: Dictionary (weight 0.9)
# T3: Corpus (weight 0.7)
# T4: Grammar patterns (weight 0.8)
# T5: LLM (weight 0.4)

# Promotion requires:
# - ≥2 T1/T2 sources, OR
# - Consensus confidence >= 0.95
```

### Verifiers

Custom verifiers can be added:

```python
from zolai.foundation.verifiers import Verifier, VerificationResult

class CustomVerifier(Verifier):
    def verify(self, candidate, evidence):
        # Custom verification logic
        passed = some_check(candidate, evidence)
        return VerificationResult(
            passed=passed,
            score=0.8 if passed else 0.2,
            notes="Custom verification"
        )

# Register custom verifier
runner = VerificationRunner()
runner.add_verifier(CustomVerifier())
```

## Review UI

### API Endpoints

```python
# Get review queue
GET /review/queue?status=pending&limit=100

# Approve a candidate
POST /review/approve
{
    "candidate_id": 123,
    "notes": "Verified against Bible and dictionary"
}

# Reject a candidate
POST /review/reject
{
    "candidate_id": 123,
    "reason": "ZVS non-compliant: uses 'pathian' instead of 'pasian'"
}

# Batch approve
POST /review/batch
{
    "action": "approve",
    "candidate_ids": [1, 2, 3, 4, 5],
    "notes": "All verified against multiple sources"
}
```

### CLI Interface

```bash
# List pending reviews
zolai foundation review list --status pending --limit 10

# Approve
zolai foundation review approve 123 --notes "Verified"

# Reject
zolai foundation review reject 123 --reason "ZVS non-compliant"

# Batch approve
zolai foundation review batch-approve 1 2 3 4 5 --notes "All verified"
```

## Cost Tracking

### Enable Cost Tracking

```python
from zolai.foundation.cost_tracker import CostTracker

tracker = CostTracker()

# Track API call
tracker.track(
    model="gemini-pro",
    operation="verification",
    input_tokens=1000,
    output_tokens=500,
    cost=0.001
)

# Get cost summary
summary = tracker.get_summary(period="day")
print(f"Total cost: ${summary['total_cost']}")
print(f"By model: {summary['by_model']}")
```

### Cost Alerts

```python
from zolai.foundation.cost_tracker import CostAlert

alert = CostAlert(
    daily_limit=10.00,  # $10/day
    monthly_limit=100.00  # $100/month
)

tracker.add_alert(alert)
```

## Monitoring

### Health Checks

```python
# API health check
GET /health

# Foundation status
GET /foundation/status

# Review queue stats
GET /review/stats
```

### Metrics

```python
# Foundation metrics
GET /foundation/metrics?period=day

# Returns:
# - words_processed
# - sentences_processed
# - verification_rate
# - promotion_rate
# - average_confidence
# - cost_per_operation
```

## Troubleshooting

### Common Issues

1. **Migration errors**: Run `python -m zolai.data.migrations` to apply pending migrations
2. **Verification stuck**: Check `foundation_batches` table for failed batches
3. **Cost exceeded**: Check `foundation_cost` table and adjust limits
4. **Review queue full**: Increase batch size or adjust thresholds

### Debug Mode

```bash
# Enable debug logging
export ZOLAI_LOG_LEVEL=DEBUG

# Run with verbose output
zolai foundation verify --verbose --batch-size 10
```

### Performance Tuning

```python
# Optimal settings for large datasets
pipeline = ETLPipeline(
    batch_size=5000,  # Larger batches for throughput
    concurrency=8,    # Match CPU cores
    max_retries=3     # Handle transient failures
)

runner = VerificationRunner(
    batch_size=1000,
    concurrency=4,
    timeout=300  # 5 minute timeout per batch
)
```

## Next Steps

1. **Run initial ETL**: `zolai foundation etl --batch-size 1000`
2. **Verify data**: `zolai foundation verify --batch-size 100`
3. **Review queue**: `zolai foundation review list`
4. **Monitor costs**: Check `/foundation/metrics` endpoint
5. **Scale up**: Increase batch sizes and concurrency as needed