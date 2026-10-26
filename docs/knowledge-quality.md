# Knowledge-quality gate

The **data-quality lane**: 43 SQL metrics over *stored facts* in `data/zolai.db` (gloss
coverage, duplication, confidence, orphan/empty ledger, script contamination, provenance
completeness, reference-format conformance), 18 byte-exact regression pins, and 7 structural
guards — plus executable done-when checks for Track 0 and R2-A..G.

It measures **rows, not model behaviour**. Model quality stays in the `zolai-eval` lane
(BLEU/F1); the only bridge is metric **M12**, which reports eval-lane health so a dead
behaviour gate cannot hide behind a green data gate.

The root cause of the `sanga = 31` bug was unreviewed auto-promotion, so the gate
deliberately contains **no LLM-based judgement**. Every threshold is a reviewed number with
SQL behind it.

| File | Role |
|---|---|
| `scripts/maintenance/knowledge_quality_report.py` | The harness (stdlib-only: `sqlite3`, `argparse`, `json`, `datetime`, `hashlib`). |
| `scripts/maintenance/knowledge_quality_pins.json` | Single source of truth: thresholds, per-metric SQL, pin expectations, guards, done-when registry. **The script hardcodes no thresholds.** |
| `tests/test_knowledge_quality_report.py` | Synthetic-fixture pytest module (66 tests, no live DB). |
| `report/kq/` | Committed per-run artifacts — the evidence trail. |

## Running it

```bash
# default: read-only, JSON + MD into report/kq/
python3 scripts/maintenance/knowledge_quality_report.py

# one track's done-when checks + the metrics they reference
python3 scripts/maintenance/knowledge_quality_report.py --track R2-C

# diff against the previous run (delta + amber→green / green→red transitions)
python3 scripts/maintenance/knowledge_quality_report.py --compare report/kq/kq-<prev>.json

# the two slow metrics only (M6c writer-scan, M13c verse-union anti-join)
python3 scripts/maintenance/knowledge_quality_report.py --deep
```

| Flag | Meaning |
|---|---|
| `--db PATH` | default `config.paths.zolai_db` (honors `ZOLAI_DB_PATH` / `ZOLAI_DATA_ROOT`) |
| `--dry-run` | **default and implied** — opens sqlite via `file:…?mode=ro` + `PRAGMA query_only`, refuses non-`mode=ro` URIs, writes nothing |
| `--persist` | opt-in: `CREATE TABLE IF NOT EXISTS knowledge_quality_runs/_metrics` + one INSERT row, single txn, requires `--yes` or interactive confirm |
| `--track ID` | run only that track's done-when checks + referenced metrics |
| `--format json\|md\|both` | default `both` |
| `--out DIR` | default `report/kq/` — committed on purpose, these files are the evidence |
| `--deep` | enable M6c + M13c |
| `--compare FILE` | diff section vs a previous run JSON |
| `--pins FILE` | alternate pins registry (used by the test fixtures) |

Exit codes: **`0`** all green/grandfathered · **`2`** amber present · **`1`** red (regression
or unmet post-track done-when) · **`3`** harness error (DB unreachable, sqlite < 3.30,
non-readonly URI).

## Reading R/Y/G

Every metric carries `direction` + `amber` + `green`:

- `higher` (coverage, conformance) — `value >= green` → green; `>= amber` → amber; else red.
- `lower` (dups, junk, empties) — `value <= green` → green; `<= amber` → amber; else red.
- `info` — reported only, never fails the run.
- `amber: null` — the amber band extends to infinity: worse than green is amber, never red.
  Used where the real red signal is a structural guard, not a moving number (e.g. M7).
- `value: null` → `absent` — table not found or empty; exit-neutral by default.

**Red on a fresh database is expected, not a fault.** On the pre-Track-0 DB the honest
distance-to-target is 29 red metrics against 0 red guards and 0 red pins. The pins and
guards are the "never regress" floor; the metric reds are the work queue.

### `dropped` vs `absent`

A dup-ratio metric whose source table is gone *or empty* reports `dropped: true` and
**green** when its pins entry sets `dropped_ok` (M4.7, M4.8, M4.9 — the tables Track 0 is
expected to retire). Without `dropped_ok` the same condition is `absent`, reported but
exit-neutral. `dropped` is reviewer-visible on purpose: retiring a table and deleting its
rows have to show up in the track report alongside the merge-delta evidence.

## Pins

| Mode | Behaviour |
|---|---|
| `MUST_NOT_REGRESS` | pinned **good** value. Any byte change → red until a reviewed `pins.json` commit updates it (citing track + evidence). |
| `MUST_IMPROVE` | pinned **bad** value (the 8 conflict headwords + 6 verified numerals). Stays amber (expected-red) until its `active_from` track lands, then red if still unmet, green once `target_sql` returns 1. |
| `ambiguous` | competing headwords (four/six/seven/ten) — never enforced, `skipped`. |

Pre-track `MUST_IMPROVE` pins that still match their bad baseline are counted
**grandfathered** and do not fail the exit code; that is what makes `exit 0` mean "all green
or legitimately grandfathered".

## Structural guards

Seven `G-*` checks are **instant-red** — they encode invariants that may never regress
independ of any threshold band: reference format conformance, reference uniqueness,
Bible-direction conformance, `word_usage` corrupt-slice shrink-only, zero Burmese-script
contamination in the six live lexicon fields, six active control tables pinned at exactly
1.00x, and the seven phoneme-core headwords never deleted.

## Baselines

Baseline values are frozen per run in the pins registry and **re-baselined at each track
boundary** (spec §7): after a track verifies, run the harness non-persisted, paste the
"after" numbers into the tracker, and update the `baseline` column in
`knowledge_quality_pins.json` in a reviewed commit. The spec document stays as the
historical anchor.

The committed artifact in `report/kq/` is the "before" of the round it was captured in;
`--compare` diffs against the *immediately previous* run, never against ancient numbers.

## Cost

Default run ≈ 43 metrics + 18 pins + 7 guards, all read-only: **~15 s** on the 2.4 GB
database (target budget ≤ 60 s). `--deep` adds the M6c writer-scan and M13c verse-union
anti-join. No network, no LLM.

## Persistence

`--persist` is the only path that constructs a read-write connection, and it is gated on
`--yes` or interactive confirmation. It creates exactly two additive tables
(`knowledge_quality_runs`, `knowledge_quality_metrics`) modeled on `db_integrity_runs` +
`corpus_quality_issues`, in a single transaction. It never touches `eval_*`,
`corpus_quality_*`, or `db_integrity_*`. Enabling it on a live database is owner-gated.
