# Skill: Local Learning Run
# Triggers: "learning run", "learn locally", "train core engines", "update rag index", "knowledge version", "verify knowledge snapshot"

## Purpose
Run one **local** learning cycle against the on-disk SQLite knowledge base:
study the corpus, learn/promote verified knowledge, retrain the POS and
syllable CRFs, incrementally re-embed changed RAG sources, and snapshot a
`knowledge_versions` row — all with a verified backup, a per-run ledger, and a
JSON report.

No cloud model, no API key, no network call is part of this cycle. The only
requirement is the local database and the local Python environment.

## Modes
| Mode | Writes | What it produces |
|------|--------|------------------|
| dry-run (**default**) | nothing to the DB | a JSON report of what *would* change; RAG step only estimates |
| `--record-dry-run` | ledger rows only | the dry-run report is recorded as history (no version, no artifact) |
| `--apply` | backup → steps → version | published models (atomic), new vectors, one `knowledge_versions` row |

`--apply` refuses to start unless the preflight passes: target ≥ 1 GB,
enough free disk for the backup, `sqlite3` on `PATH`, the required tables
present, and `PRAGMA quick_check == ok`.

## Steps (in order)
1. `preflight` — size, disk, sqlite CLI, tables, quick_check, dependency probe
2. `backup` — `sqlite3 .backup`, size-ratio gate, sha256 (skipped in dry-run)
3. `study` — row counts, review statuses, latest version
4. `knowledge_bible` — repair/attest/wire/dedupe/grammar from Bible
5. `bible_levels` — hierarchical learning (line → paragraph → document →
   corpus) into `corpus_learning_layers` + paragraph openers; opt out with
   `--no-levels`
6. `knowledge_discovery` — candidate discovery
7. `knowledge_promote` — promote hypotheses to claims (evidence thresholds)
8. `knowledge_regression` — snapshot regression checks
9. `train_pos` — POS CRF → staged → gated → published
10. `train_syllable` — syllable CRF → staged → gated → published
11. `rag_incremental` — bounded re-embed of changed sources
12. `version_snapshot` — `knowledge_versions` row + verification
13. `report` — JSON report on disk

A step reporting `FAILED` stops the run; the pre-run backup is the restore path.

## Commands
```bash
# dry-run everything (default)
zolai learn run

# bounded apply: one small batch, counts-based manifest, explicit backup dir
zolai learn run --apply --limit 50 --max-chunks 128 \
  --counts-manifest --backup-dir data/backups

# retrain only the core engines (no corpus learning, no RAG)
zolai learn run --apply --steps preflight,backup,train_pos,train_syllable,version_snapshot

# skip the RAG step but keep everything else
zolai learn run --apply --no-rag

# hierarchy only: study every source at line/paragraph/document/corpus grain
zolai learn levels                     # dry-run: prints the JSON summary
zolai learn levels --apply             # writes layers + paragraph openers
zolai learn levels --sources bible,wiki --max-docs 200

# history and verification
zolai learn status --limit 5
zolai learn versions --limit 10
zolai learn verify v2026.10.28.1
```

All commands print JSON by default; add `--no-json` for the human summary.

## Exit Codes
| Code | Meaning |
|------|---------|
| 0 | success (dry-run or apply) |
| 1 | step failed (`error_class` names the failure) |
| 2 | preflight/config refusal (nothing written) |
| 3 | version verification drift or unknown/malformed version |
| 130 | interrupted (Ctrl-C); the run is recorded as ABORTED |

## Gates and Honesty
- A model is published only when `macro_f1` (POS) / `boundary_f1` (syllable) is
  not NaN and has not dropped more than `--max-metric-drop` (default 0.01)
  below the previous run for the same set; the rejected staging file is deleted.
- Publishing is atomic (`os.replace`) and keeps `previous-<name>` for rollback.
- POS gold is also its training source, so the full-set score is labelled
  `in_sample`; only the hold-out split is a generalization number.
- Missing syllable splits or an empty `pos_gold_v0` produce `SKIPPED`, never a
  fabricated model.
- Long-lived API processes must be restarted to load newly published models.

## Safety Rules
- Never run `--apply` without reading the dry-run report first.
- Never widen `--limit`/`--max-chunks` beyond what the backup and disk check can
  cover; the RAG step is always bounded.
- Never edit the database while a run is in progress; the ledger reports a
  `RUNNING` run and preflight refuses to start another.
- Reports and tool responses carry statuses, counts and hashes only — no
  absolute paths, no source text, no secrets.

## Agent Tools (admin tier, read-only)
| Tool | Scope | Answers |
|------|-------|---------|
| `learning_run_status` | `pipeline:read` | what ran, step counts, artifact statuses |
| `learning_version_history` | `eval:read` | which versions exist and what produced them |
| `learning_rag_status` | `rag:read` | vector counts, watermarks, stale source ids |

These tools never start a run and never prune vectors — a tool call cannot
trigger a backup, a training run or a delete.
