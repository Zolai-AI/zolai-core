# Monitoring & Database Integrity — Runbook

**Scope:** how the Prometheus/Grafana stack is wired into the API, the full
metric inventory, the alert rules, and the ACID hardening applied to the
canonical 2.3GB SQLite store (`data/zolai.db`).

| | |
|---|---|
| Compose file | `docker-compose.monitoring.yml` |
| Provisioning / dashboards | `ops/` |
| Instrumentation | `zolai/monitoring/` |
| REST surface | `zolai/api/metrics_router.py` (mounted **before** the catch-all route) |
| DB hardening | `zolai/data/database.py`, `zolai/data/integrity.py`, `zolai/data/migrations.py` |
| Parity gate | `tests/test_alert_rules_parity.py` |

---

## 1. Quick start

```bash
# API (must listen on 0.0.0.0 so the container can reach the host)
.venv/bin/uvicorn zolai.api.server:app --host 0.0.0.0 --port 8000

# Stack (Prometheus 3.15.0 + Grafana 13.2.3)
docker compose -f docker-compose.monitoring.yml up -d
```

| Service | URL | Credentials |
|---|---|---|
| API metrics | http://localhost:8000/metrics | — |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | `${GRAFANA_ADMIN_USER:-admin}` / `${GRAFANA_ADMIN_PASSWORD:-admin}` |

Stop: `docker compose -f docker-compose.monitoring.yml down`
(data survives in the `prometheus-data` / `grafana-data` volumes; add `-v` to
discard).

**Env-only secrets** (never committed): `GRAFANA_URL`, `GRAFANA_API_KEY`
(annotation push), `GRAFANA_ALERT_WEBHOOK_URL`, `GRAFANA_ADMIN_USER`,
`GRAFANA_ADMIN_PASSWORD`. The compose file only references `${…}` forms with
defaults, so `.env.example` placeholders are the documented values.

Linux note: Prometheus reaches the host through
`extra_hosts: host.docker.internal:host-gateway`; without it the target shows
`connection refused`.

---

## 2. HTTP endpoints

Mounted in `zolai/api/server.py` **before** `@app.get("/{path:path}")`, so no
route is shadowed.

| Path | Method | Returns |
|---|---|---|
| `/metrics` | GET | Prometheus text exposition 0.0.4 (`sample_now()` first, so size/WAL gauges are fresh) |
| `/api/metrics/summary` | GET | `http` (requests, error_rate, p50/p95/p99), `db` (query_p95_ms, rows_total, size_bytes, wal_bytes), `business`, `eval.latest_run` |
| `/api/metrics/health` | GET | `status: ok\|degraded`, `db` (writable, wal_mode, foreign_keys, integrity), `disk`, `uptime_s`, `version` — **503** when degraded |
| `/api/metrics/eval` | GET | latest `eval_runs` row: `set_name`, `created_at`, `case_count`, `duration_ms`, `gate_passed`, `metrics`, `source: db\|none` |
| `/api/metrics/performance` | GET | `?window=5m` → `window_s`, `samples`, `p50/p90/p95/p99/max`, `by_route` |
| `/api/metrics/alerts` | GET | `evaluated_at`, `rules[]` (name, severity, threshold, expr, state, value, since_s), `active_count` |
| `/api/metrics/info` | GET | `version`, `python`, `commit`, `started_at`, `db_backend`, `metrics.enabled`, `metrics.rules_url` |
| `/api/metrics/annotations` | GET | `{items:[…]}` stored in `monitoring_annotations` |
| `/api/metrics/annotations` | POST | Grafana-compatible body (`time`, `title`, `text`, `tags[]`, `kind: deploy\|eval\|manual`, `dashboardId`, `panelId`) → **201** `{id, grafana_id}`; pushed to Grafana when `GRAFANA_URL` + `GRAFANA_API_KEY` are set |
| `/api/metrics/annotations/{id}` | PUT / DELETE | partial update (`dashboard_id`/`panel_id` preserved) / `{deleted}`; **404** when unknown |
| `/health` | GET | existing contract **plus** `uptime_s` (additive, non-breaking) |

Lifespan (`_start_monitoring` / `_stop_monitoring`): startup FK guard →
`DB_INTEGRITY_STATUS`, `zolai_build_info`, sampler thread start; shutdown stops
the sampler.

---

## 3. Metric inventory

Naming: `zolai_<domain>_<metric>` — counters end `_total`, durations in
`_seconds`, metadata `_info`. `process_*` / `python_*` come from
`prometheus-client` defaults.

### HTTP (`MetricsMiddleware`)

| Metric | Type | Labels |
|---|---|---|
| `zolai_http_requests_total` | counter | `method`, `route`, `status` |
| `zolai_http_request_duration_seconds` | histogram | `method`, `route` |
| `zolai_http_requests_in_flight` | gauge | — |

### Database (`db_metrics` listeners)

| Metric | Type | Labels |
|---|---|---|
| `zolai_db_query_duration_seconds` | histogram | `operation` |
| `zolai_db_query_errors_total` | counter | `error` |
| `zolai_db_size_bytes` / `zolai_db_wal_bytes` | gauge | — |
| `zolai_db_integrity_status` | gauge | — (1 = last FK/integrity check passed) |
| `zolai_db_table_rows` | gauge | `table` |

### Linguistic analysis (`zolai/monitoring.track_operation`)

| Metric | Type | Labels |
|---|---|---|
| `zolai_analysis_operations_total` | counter | `operation` |
| `zolai_analysis_duration_seconds` | histogram | `operation` |

### Business / corpus

| Metric | Type | Labels |
|---|---|---|
| `zolai_words_translated_total` | counter | `direction` |
| `zolai_corpus_sentences` | gauge | — (from `translations`) |
| `zolai_dictionary_entries` | gauge | — (from `dictionary`) |
| `zolai_bible_verses` | gauge | — |

### Evaluation / alerting / metadata

| Metric | Type | Labels |
|---|---|---|
| `zolai_eval_runs_total` | counter | `set_name` |
| `zolai_eval_metric_value` | gauge | `set_name`, `metric` |
| `zolai_eval_last_run_timestamp_seconds` | gauge | — |
| `zolai_alerts_active` | gauge | — |
| `zolai_alert_state` | gauge | `rule` (1 firing, 0 ok) |
| `zolai_build_info` | gauge | `version`, `python`, `commit` |

**Label cardinality policy**

- `route` is always a **route template** (`/api/dict/{word}`), never a raw path;
  unmatched requests report `unmatched`. Cap: `MAX_TRACKED_ROUTES = 256`.
- `operation` is always an **operation class** (`select`/`insert`/`update`/
  `delete`/`other`) — SQL text is never a label.
- `table` is capped to `CURATED_TABLES` (12 tables).
- Buckets: HTTP `(0.001 … 10s)`, DB `(0.0005 … 5s)`, analysis `(0.001 … 10s)`.

**Sampling.** `zolai/monitoring/background.py` runs a daemon sampler every
`SAMPLE_INTERVAL_S = 300` (DB size, WAL, row gauges, corpus/dictionary/Bible
counts); results are cached `CACHE_TTL_S = 60`. `/metrics` and
`/api/metrics/summary` call `sample_now()` first so synchronous callers never
serve a stale file-size gauge.

---

## 4. Dashboards

Provisioned by `ops/grafana/provisioning/dashboards/dashboards.yml` into the
**Zolai** folder (30s refresh, UI edits allowed).

| UID | Title | Panels |
|---|---|---|
| `api-overview` | Zolai API Overview | requests (5m), error rate, p95, in-flight, request rate by route, p50/p95/p99 latency (+1s threshold), error rate by route, status-code bars, latency heatmap |
| `database-storage` | Zolai Database & Storage | DB size gauge, WAL size, integrity check (0 FAILED / 1 OK mappings), curated rows, query p95/p99 (+50ms threshold), query rate by operation, rows by table, size over time |
| `linguistic-pipeline` | Zolai Linguistic Pipeline | analysis ops (5m), translations served, corpus sentences, dictionary entries, analysis op rate, analysis p95, translations by direction, eval metrics, Bible verses |

Template variables: `$instance` on all three; `$route` on `api-overview`;
`$operation` + `$set` on `linguistic-pipeline`; `$table` on `database-storage`.
`api-overview` and `database-storage` include tag-based Grafana annotation
queries (`deploy`, `eval`, `manual`).

Validate after `up -d`:

```bash
curl -s -u admin:admin "http://localhost:3000/api/search?type=dash-db"
curl -s -u admin:admin "http://localhost:3000/api/dashboards/db?uid=api-overview"
```

---

## 5. Alerting

The **same three thresholds exist in three places**, and CI keeps them in sync:

1. `zolai/monitoring/alerts.py` → `RULES` (feeds `/api/metrics/alerts`)
2. `ops/prometheus/rules.yml` (Prometheus ruler)
3. `ops/grafana/provisioning/alerting/rules.yml` (Grafana-managed rules)

| Rule | Severity | Threshold | For | Summary |
|---|---|---|---|---|
| `http_error_rate_high` | critical | 5xx ratio > 0.05 over 5m | 5m | HTTP 5xx error rate above 5% for 5m |
| `http_latency_p95_high` | warning | HTTP p95 > 1s (5m rate) | 5m | HTTP p95 latency above 1s for 5m |
| `db_query_p95_high` | warning | DB p95 > 0.05s (5m rate) | 5m | DB p95 query latency above 50ms for 5m |

`tests/test_alert_rules_parity.py` asserts name set, threshold, severity,
`for`, summary and metric-family overlap across all three copies, and that the
local evaluator fires on a synthetic bad window. **To change a threshold:
change `RULES` first, then mirror it in both YAML files — the test fails until
they match.**

Notification routing: Grafana contact point `zolai-ops`
(`webhook` → `GRAFANA_ALERT_WEBHOOK_URL`, default
`http://host.docker.internal:8000/api/metrics/annotations`) with a notification
policy routing `severity=critical` (4h repeat) and `severity=warning` (24h
repeat) to that same point.

```bash
# rule file validity (Grafana/Prometheus rulers agree)
docker compose -f docker-compose.monitoring.yml run --rm --no-deps \
  --entrypoint promtool prometheus check rules /etc/prometheus/rules.yml
```

---

## 6. Database hardening (ACID)

### Per-connection PRAGMAs

`foreign_keys` is **per-connection and not persistent**, so a once-at-creation
PRAGMA is a bug. `zolai/data/database.py` installs an
`@event.listens_for(engine, "connect")` listener that runs on **every** new
pooled connection:

| PRAGMA | Value | Why |
|---|---|---|
| `busy_timeout` | `30000` | wait instead of raising `SQLITE_BUSY` |
| `journal_mode` | `WAL` | persistent; readers never block the writer |
| `synchronous` | `NORMAL` | WAL-appropriate durability — a crash can lose the last transactions but cannot corrupt the DB (see tradeoff below) |
| `foreign_keys` | `ON` | ABORT enforcement, unless the startup FK guard downgraded to `deferred` |

**`synchronous=NORMAL` tradeoff:** with WAL, a power loss can drop recently
committed transactions that are still only in the WAL; the file stays
structurally consistent. `FULL` trades roughly an order of magnitude of write
throughput for that last-commit guarantee — switch only if the store is
replicated/backed up elsewhere.

### Pooling and writers

- `QueuePool(pool_size=10, max_overflow=20, pool_timeout=30)` with
  `connect_args={"check_same_thread": False, "timeout": 30}`.
- `immediate_transaction()` — raw DBAPI connection, `BEGIN IMMEDIATE`
  (acquires the write lock up front instead of deadlocking at COMMIT), with
  `busy` retry backoff on top of the 30s timeout.
- `write_session()` — the session form of the same writer path.
- Plain `session()` keeps its normal rollback semantics; **reads are untouched**.

### Foreign-key policy + integrity checks

`zolai/data/integrity.py`:

| Function | Cost | Used by |
|---|---|---|
| `foreign_key_check()` | fast (scans FK participants only) | `startup_guard`, `/api/metrics/health`, `zolai db integrity` |
| `startup_guard()` | fast | API lifespan — clean store keeps `foreign_keys=ON`, violations switch policy to `deferred` and log loudly |
| `full_integrity_check()` | **tens of seconds** on 2.3GB | CLI / on-demand only — **never** a startup path |

Every completed run is recorded in `db_integrity_runs` (best-effort, never
raises if the table is missing). The `deferred` policy exists so pre-existing
orphans in the shared store can never turn an additive migration into a write
failure — new connections then run with `foreign_keys=OFF` until an operator
repairs the data.

```bash
.venv/bin/zolai db integrity            # fast FK scan (exit 1 on violations)
.venv/bin/zolai db integrity --full     # full PRAGMA integrity_check (slow)
.venv/bin/zolai db integrity --json     # machine-readable report
```

### Monitoring schema + de-duplication

`create_monitoring_tables()` (wired into `run_all_migrations`) creates, all
additive:

- `monitoring_annotations` — NOT NULL / CHECK on `kind`
- `eval_runs` — `set_name REFERENCES eval_sets(set_name)` (real FK)
- `db_integrity_runs`
- `CREATE UNIQUE INDEX IF NOT EXISTS ux_fraw_content_hash ON
  foundation_raw_corpus(content_hash)` — safe: `foundation_raw_corpus` has 0
  duplicate hashes (verified on the live store)

Live store verification: `PRAGMA foreign_key_check` → **0 violations**, so FK
enforcement may be enabled on startup.

### Services / repository audit

- No bare `.commit()` in `zolai/data/services/` — all writers go through
  `engine.begin()` or the repository `transaction()` CM, so exceptions roll
  back cleanly.
- `get_session()` is unused in `zolai/data/services/` (the only matches are
  private helpers `JSONLPipeline._get_session` in `zolai/core/`).

---

## 7. Deferred constraints on legacy tables (and the 12-step rebuild)

**Why deferred.** SQLite cannot add `NOT NULL`, `CHECK` or `REFERENCES`
constraints to an existing table — only `ALTER TABLE … ADD COLUMN` (which
rejects `REFERENCES` on non-integer/`WITHOUT ROWID` cases and cannot add
checks). Adding them requires a **table rebuild** (new table → copy → drop →
rename). On the shared 2.3GB store with 3.3M rows that is a destructive
operation: any locked/failed copy risks data loss, and every concurrent reader
(zolai-core API, JSONL pipeline, other repos) would break mid-flight.
Therefore constraints on **legacy** tables are deferred; only **new** tables
(`monitoring_annotations`, `eval_runs`, `db_integrity_runs`) carry them today.

**Rebuild migration path — behind a flag, post-backup, 12 steps:**

1. Take a verified backup (`python -m zolai.data.database backup` →
   `<db>.bak` via the SQLite backup API; then checksum + size match, and a
   `PRAGMA integrity_check` on the copy); restore drill on a copy before
   touching prod.
2. Freeze writers: stop the API/CLI pipelines that take the write lock.
3. Enable the `--rebuild-constraints` flag (off by default; dry-run prints the
   DDL only).
4. Record row counts + `PRAGMA foreign_key_check` + `PRAGMA integrity_check`
   baseline into `db_integrity_runs`.
5. `PRAGMA foreign_keys=OFF` for the session (constraint rebuild must not fire
   mid-copy) — keep `busy_timeout` high.
6. For each target table: `CREATE TABLE … _rebuild` with the new
   `NOT NULL` / `CHECK` / `REFERENCES` clauses, plus a `CHECK` translated from
   the application-side validation it replaces.
7. Copy with `INSERT INTO t_new SELECT …`, applying an explicit default for
   rows violating a new `NOT NULL` (never silently drop rows — report counts).
8. Verify: `SELECT count(*)` old == new, `PRAGMA foreign_key_check` == 0,
   spot-check `PRAGMA integrity_check` on the new table.
9. `DROP TABLE t; ALTER TABLE t_new RENAME TO t;` inside one transaction
   (`BEGIN IMMEDIATE`), then recreate indexes/triggers from the captured
   `sqlite_master` definitions.
10. Re-apply any `AUTOINCREMENT`/sequence state and re-create FTS/shadow
    tables that referenced the original.
11. Turn `PRAGMA foreign_keys=ON`, run `zolai db integrity --full`, compare
    against the baseline captured in step 4.
12. Release the write freeze; keep the backup until N subsequent healthy runs
    (default N=7). On any failure: roll forward to the backup from step 1.

Until that path ships, the enforcement points are: schema-level constraints on
**new** tables, the startup FK guard, `zolai db integrity`, and application-side
validation.

---

## 8. Validation

```bash
.venv/bin/ruff check zolai tests
.venv/bin/pytest -q tests/test_acid.py tests/test_monitoring_api.py \
    tests/test_monitoring_metrics.py tests/test_alert_rules_parity.py
.venv/bin/pytest -q                      # full suite, 0 failed

# smoke
curl -s localhost:8000/metrics | head -40
curl -s localhost:8000/api/metrics/summary
docker compose -f docker-compose.monitoring.yml config --quiet
docker compose -f docker-compose.monitoring.yml up -d
curl -s localhost:9090/-/ready
curl -s localhost:3000/api/health
```

| Test file | Asserts |
|---|---|
| `tests/test_acid.py` | PRAGMAs on fresh pooled connections, FK rejects orphans, rollback leaves no partial rows, unique `content_hash`, 4 threads × 50 writes without `SQLITE_BUSY`, integrity smoke, `zolai db integrity` CLI |
| `tests/test_monitoring_metrics.py` | exposition format + core families, **deltas** (global registry is shared), route-template labels, build info |
| `tests/test_monitoring_api.py` | all endpoints schema-valid, annotation CRUD + Grafana body, `/health` contract, health **503** on unwritable path |
| `tests/test_alert_rules_parity.py` | `RULES` == Prometheus YAML == Grafana YAML; evaluator fires on a synthetic bad window |

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Prometheus target `down`, `connection refused 172.17.0.1:8000` | API bound to `127.0.0.1`, or missing `extra_hosts: host-gateway` — start uvicorn on `0.0.0.0` |
| `/api/metrics/health` returns 503 | degraded = DB not writable, last integrity check failed, or disk not writable/free space unreadable — inspect the `db` / `disk` sections |
| `zolai_db_integrity_status` is 0 | startup guard found FK violations; run `zolai db integrity` for details, repair, restart |
| A metric family is missing from `/metrics` | counters/histograms emit no samples until first use — hit the endpoint that exercises it, or call `/api/metrics/summary` (runs `sample_now`) |
| Grafana rule file rejected | keep the `expr` single-line and ending in `> <threshold>`; the parity test parses that shape |
| Parity test fails after a threshold change | update `zolai/monitoring/alerts.py` **and** both `ops/**/rules.yml` copies |
| Grafana data gone after `down -v` | expected — `-v` removes the `grafana-data` volume |

### Known limitations

- Full `ruff check zolai/data` reports pre-existing errors: `pyproject.toml`'s
  `[tool.ruff] exclude = [... "data" ...]` matches `zolai/data/**` by basename,
  so the gate `ruff check zolai tests` skips that subtree (out of scope here).
- `python -m zolai.cli.main` defines `serve`/`desktop` *after* the
  `__main__` guard, so that entrypoint lacks them; the `zolai` console script
  imports the module fully first and is unaffected.
- Legacy-table constraint rebuild (§7) is documented, not yet implemented.
