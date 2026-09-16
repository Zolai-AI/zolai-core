# Foundation Principles

Core philosophy governing the Zolai Foundation data layer. These principles derive from the reference ontology's final data flow and core philosophy.

---

## 1. No Direct LLM → Canonical

**Rule:** No LLM output writes directly to canonical tables.

**Rationale:** LLMs hallucinate. The Zolai language has strict ZVS 2018 orthography, SOV word order, ergative `in`, and tone-dependent polysemy that LLMs frequently violate.

**Enforcement:**
- All LLM outputs enter as `Candidate` objects in `foundation_evidence`
- Canonical promotion requires ≥2 independent evidence sources (dictionary + Bible, or Bible + corpus, etc.)
- `Verifier` ABC implementations validate candidates before consensus

---

## 2. Evidence Gating

**Rule:** Every canonical fact must carry verifiable evidence.

**Evidence Tiers (strongest → weakest):**
| Tier | Source | Weight |
|------|--------|--------|
| T1 | Bible parallel verses (31,102 EN↔ZO) | 1.0 |
| T2 | Community dictionaries (TongDot, TongSan, our cleaned master) | 0.9 |
| T3 | Web corpus attestations (frequency ≥5) | 0.7 |
| T4 | Grammar pattern matches (5,482 patterns) | 0.8 |
| T5 | LLM generation (with RAG context) | 0.4 |

**Implementation:** `Evidence` dataclass carries `source`, `tier`, `confidence`, `provenance_hash`.

---

## 3. Versioning & Provenance

**Rule:** All canonical tables are versioned and auditable.

**Schema requirements for every canonical table:**
```sql
version INTEGER NOT NULL DEFAULT 1,
source_hash TEXT NOT NULL,        -- SHA256 of input sources
verified_at TIMESTAMP,            -- When consensus was reached
verified_by TEXT,                 -- 'consensus:v1' | 'human:reviewer' | 'auto:threshold'
evidence_ids TEXT,                -- JSON array of foundation_evidence.rowids
```

**Audit log:** Every canonical write appends to `data_audit_log` (already exists, 24,762 rows).

---

## 4. RAW → Staging → Canonical Separation

**Three-layer pipeline — no shortcuts:**

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│    RAW      │────▶│  STAGING    │────▶│  CANONICAL  │
├─────────────┤     ├─────────────┤     ├─────────────┤
│ JSONL files │     │ Cleaned,    │     │ Verified,   │
│ Web crawls  │     │ deduplicated│     │ versioned,  │
│ PDF extracts│     │ normalized  │     │ evidence-   │
│ Bible USX   │     │ (temp)      │     │ gated       │
└─────────────┘     └─────────────┘     └─────────────┘
   Ingest           Build               Promote
   (append-only)    (idempotent)        (consensus-gated)
```

- **RAW:** Append-only, immutable, provenance-tracked
- **STAGING:** Transient, rebuildable from RAW, no serving reads
- **CANONICAL:** Serving layer reads ONLY from here (enforced by `test_dbfirst_compliance.py`)

---

## 5. Batch + Adaptive Verification

**Rule:** Verification scales with confidence, not fixed rules.

**Adaptive threshold logic:**
```
confidence ≥ 0.95  → auto-promote to canonical
confidence ≥ 0.70  → batch verify (majority vote + evidence weighting)
confidence < 0.70  → human review queue
```

**Batch runner:** Processes candidates in configurable batches (default 100), with:
- Majority vote among candidates for same fact
- Evidence weighting by tier (T1=1.0, T2=0.9, T3=0.7, T4=0.8, T5=0.4)
- Confidence threshold decision (configurable per fact type)

---

## 6. ZVS 2018 as Ground Truth

**Rule:** All outputs enforce ZVS 2018 orthography.

| Forbidden | Correct | Context |
|-----------|---------|---------|
| `pathian` | `pasian` | God |
| `ram` | `gam` | earth/land |
| `fapa` | `tapa` | life/son |
| `bawipa` | `topa` | Lord |
| `siangpahrang` | `kumpipa` | Savior |
| `cu/cun` | `tua` | that (conjunction) |
| `suah` | `suahtakna` | holiness |
| `nunnak` | `nuntakna` | life |

**Enforcement:** `ZVSValidator` runs on all canonical promotions (already exists, 192 tests pass).

---

## 7. Bible as Corpus, Not Religion

**Rule:** The Bible is our primary training corpus because it is the **only complete, trusted, EN/ZO parallel corpus** for Tedim Zolai.

- 31,102 parallel verses (EN↔ZO) — no other source comes close
- Complete text: all 66 books, all registers (narrative, poetry, dialogue, law)
- Multiple versions: TDB77, Tedim2010, Hakha, Falam, Paite
- Community-validated: decades of translation work by native speakers
- Publicly available: open access for language preservation

**Usage:** Bible verses provide T1 evidence tier. No religious interpretation in Foundation layer.

---

## 8. SOV + Ergative `in` Invariants

**Rule:** All generated/validated Zolai text must satisfy:

- Word order: **SOV** (Subject–Object–Verb)
- Ergative marker: **`in`** marks transitive agent
- Negation: **`kei`** for ALL persons (not `lo` with agreement)
- Questions: **`hiam`** yes/no; **`bang hang` + V + S + `hiam`** content
- Future: **`ding`**; negative future: **`kei ding`**

---

## 9. Tone-Aware Processing

**Rule:** Tone sandhi (19 rules) and 4-tone system must be tracked.

- T1=High, T2=High Falling (sandhi only), T3=Low, T4=Creaky
- Written Zolai does NOT mark tones — same spelling = different meanings
- `khem` T1=lie/deceive, T3=thin/weak; `zu` T1=alcohol, T4=rain (with `guah-`)
- Foundation analyses carry tone ambiguity flags where applicable

---

## 10. Minimal Dependencies, Maximum Reproducibility

**Rule:** Foundation module has zero network dependencies at runtime.

- All engines (tokenizer, syllable, POS, morphology) are local
- `Verifier` ABC allows injecting LLM verifiers for batch jobs, but default is `NullVerifier` (no-op)
- Consensus is pure Python (majority vote + evidence weighting)
- Gold evaluation runs offline against local JSONL fixtures

---

## 11. Human-in-the-Loop Verification

**Rule:** Low-confidence candidates require human review before canonical promotion.

- Candidates with confidence < 0.70 enter the review queue
- Human reviewers can approve, reject, or request modifications
- All human decisions are logged for audit and model improvement
- Review queue is accessible via API and CLI

---

## 12. Cost-Aware Processing

**Rule:** All LLM operations are tracked for cost analysis and budget management.

- Per-model cost tracking (input/output tokens, API calls)
- Batch operations report total cost and per-item cost
- Cost thresholds can trigger alerts or automatic throttling
- Historical cost data enables budget forecasting

---

## 13. Production-Ready Deployment

**Rule:** Foundation Engine must be deployable with minimal configuration.

- Docker support for consistent environments
- Health checks and graceful shutdown
- Configurable batch sizes and concurrency limits
- Comprehensive logging and monitoring endpoints