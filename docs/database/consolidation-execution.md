# R2-A Consolidation — Execution Report (2026-10-28)

Plan: `data/conductor/plan-r2a-db-consolidation.md` · Recon: `data/conductor/recon-db-single-source.md` ·
Runner: `scripts/maintenance/consolidation_r2a.py` + `scripts/maintenance/r2a_lib.py` ·
Fixture suite: `tests/test_consolidation_r2a.py` (25 tests, synthetic `tmp_path` only — no live DB/network/LLM).

## Execution order (all gates green)

1. **fuser single-writer check** — clean (exit 1 = no holders).
2. **Fresh pre-A backup** — `data/backups/zolai-2026-10-28_1935.db.gz` (1.1G) +
   **verify drill** `zolai-2026-10-28_1942`: restored to /tmp, dictionary 84,490 / bible_verses 31,102 /
   vocabulary 104,906 / translations 207,623 — all match plan §0.
3. **dry-run** (ro) — exit 0, every projection matched plan §0.
4. **apply** — every item in one `BEGIN IMMEDIATE` batch each, before/after count asserts inside,
   `r2a_batch` provenance + import_log entry per item.
5. **verify** (ro) — exit 0, 10/10 items pass, idempotent re-runs skip.
6. **vacuum** — `VACUUM INTO` tmp + `PRAGMA integrity_check=ok` + atomic swap; pre-vacuum file kept at
   `data/backups/zolai.db.pre-vacuum-20261028T133227Z`.
7. **KQ tail** — `knowledge_quality_report.py --track R2-A` (ro, no `--persist`), report
   `report/kq/kq-20261028T133255Z.json`.

## Before / after per D-item (live)

| D-item | Table / metric | Before | After | Notes |
|---|---|---|---|---|
| D13 | translations_import dot refs | 135,497 | 0 | `replace('.',' ')` |
| D13 | translations_import NAH refs | 200 | 0 | `NAH.`→`NAM.` prefix rewrite, then dots |
| D13 | translations_import empty refs | 14 | 14 | intentionally kept (documented) |
| D13 | unresolved non-empty refs | — | 0 | asserted against bible_verses |
| D4–D13 archive | 26 `*_import` tables | 1,925,577 rows | archived | `zolai-import-archive-2026-10-27.db` (571 MB) + sha256 manifest; per-table count equality asserted |
| D14 | translations en→my split | 207,623 (150,965 en→my) | translations 56,658 + translations_news 150,965 | news 76,862 + parallel 74,103; non-Bible directions residual 0 |
| D14 | articles | 15,649 | 15,648 | 1 content-dup dropped (ids 8725/9105, keep MIN) |
| D14 | articles NULL content_hash | 9,278 | 0 | double-hex sha256 backfill |
| D14 | articles namespace | — | news 9,277 / lessons 4,163 / legacy 2,208 | discriminator: source_file='zomidaily' / language='zolai_simbu' / protected |
| D14 | protected legacy articles | 2,208 | 2,208 | triple-predicate guard asserted |
| D6 | word_alignments | 385,120 | 384,801 | 319 exact-pair collapse (identity gate: 319 collapsible / 0 skipped) |
| M4.13 | zolai_bible_analysis | 30,758 | 30,758 | **706 groups all skipped by full-row-identity gate** (book-code dialect collisions) → `data/conductor/r2a-skiplist-zba.json` |
| D16 | import tables dropped | 26 | 0 | blocked-until-archive gate held |
| D16 | import_log | 95 entries (46 completed / 49 failed) | 189: archived 45 / census 87 / completed 1 / failed 49 / r2a_batch 9 | alias→canonical renames, recount, census uuid entries, provenance row_count>0 only; **M8a mismatches 7→0** |
| D15 | phase-2 empty drops | 4 | 0 | dictionary_enhanced / vocabulary_enhanced / bible_verses_enhanced / proverbs_idioms; KEEP set (ngram, knowledge_vectors, gemini_model_results, zz1, zz2) intact |
| — | redundant indexes | 13 planned | 13 dropped | keep-index existence guard asserted |
| D17 | DB file size | 3,368,456,192 B | 2,315,354,112 B | **~1,006 MiB reclaimed**; freelist 243,309 pp → 0 |
| — | bible_verses canonical | 31,102 | 31,102 | **never touched**; 545 import-only refs live in archive only |

## KQ tail vs frozen baseline (`kq-20261026T224915Z.json`)

| Pin | Baseline | Now | Verdict |
|---|---|---|---|
| M8a import reconciliation | red (7) | **green (0)** | fixed by D16 rebuild |
| M4.12 articles dup ratio | red | **green (1.0)** | dup pair collapsed |
| M4.1–M4.11 import-table dup ratios | red | **absent** (neutral) | tables archived+dropped |
| M4.13 zba dup ratio | red | **red (1.0235)** | **owner gate** — 706 dialect-collision groups skipped by identity gate; skip-list filed; needs book-code normalization round |
| M13c verse-union gap | — | skipped | import-only refs archived (anti-join needs archive-aware query) |
| non-owned reds (M1/M1b/M2/M5b/M6a/M6b/M8b/M8c/M8d/M9d/M9e …) | red | not grown | R2-A track run only measures R2-A pins; no cross-track regressions |

Report: 27 green / 6 amber / 2 red (+6 grandfathered), exit 1 — the honest state: the only in-scope red is
**M4.13**, which the plan explicitly defers to the owner (merge vs normalize book codes).

## Flags for follow-up tracks

- **dictionary_my_import (7,840 rows) = the LOST Myanmar load** — archived with the other 25 import
  tables; flagged for **R2-C** (reload from source with provenance).
- **M4.13 owner gate** — `data/conductor/r2a-skiplist-zba.json` (706 groups). Resolve via R2-A follow-up
  (book-code dialect normalization: `1 C` vs `2CH`, EZE vs EZK) before any merge.
- **`AI_BRAIN_WAKE_CMD`**-style env drifts are out of scope here; nothing in this round touches services.

## Never-touched verification (post-run)

- bible_verses canonical = 31,102 ✓ · ZOMIDAILY word_usage sentinel 209,538 ✓ · phrase:* 45,597 ✓ ·
  protected 2,208 articles ✓ · KQ `--persist` never run ✓ · archive DB manifest sha256 recorded ✓.
