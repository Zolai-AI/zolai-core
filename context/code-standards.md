# Code Standards — Zolai Core

- **Languages:** Python (ruff), Bash (deploy scripts), YAML.
- Ruff enabled (`ruff check zolai/`); keep Modern/Pythonic style, type hints on public API.
- Commit style: Conventional Commits.
- Keep secrets in `.env` only — never commit keys/tokens.
- Enforce ZVS 2018 orthography on all text output.
- Tests must pass before push (`python -m pytest tests/ -q`).
- Do not commit generated artifacts (`__pycache__`, `.venv`, `node_modules`, `data/`).

## Timestamps

One serialisation, and only one: **ISO-8601 UTC with an explicit offset**
(`2026-10-28T15:23:03.160180+00:00`). `zolai/shared/clock.py` is the only place
that chooses it — `stamp()` for a string, `now()` for a datetime, `parse()` to
read one back.

- Never write a local `_now()` / `_utc_now()` / `_stamp()` helper. Each one that
  appeared before this rule picked a different format, and the formats do not
  compare: TEXT timestamps sort as bytes, and `" "` is `0x20` while `"T"` is
  `0x54`, so a column holding both ranks a 01:00 `T` row above a 23:59 space row
  and `WHERE col >= '…T…'` silently drops every space row of that day.
- Never inline `strftime("%Y-%m-%d %H:%M:%S")` (or the single-quoted variant).
  An unnamed one-liner is a `_stamp()` that dodged the helper scan — that is
  exactly how `data_audit_log.changed_at` accumulated 5,977 space-format rows
  beside 40,067 canonical ones. Use `clock.stamp()`.
- Never `datetime('now')` or `CURRENT_TIMESTAMP` in SQL, including inside a
  column `DEFAULT` — SQLite answers those in the space format, with no offset.
  Use `clock.SQL_DEFAULT` / `clock.SQL_NOW`.
- Never `datetime.utcnow()` — naive and deprecated. Naive also means the
  *consumer* decides: `datetime.fromisoformat(x).timestamp()` reads a naive
  string as machine-local, so the same instant lands six and a half hours apart
  depending on where the server ran.
- Date-only values (`last_activity_date`) are calendar days, not instants. Keep
  them date-only; do not stamp a midnight onto them.

Enforced by `tests/test_timestamp_clock.py::TestPackageWritesOneFormat` (scans
`zolai/**/*.py` with comments and docstrings masked, so a string literal cannot
hide behind a comment) and by the two schema tests in the same file.

The auth lane (`zolai/auth/oauth.py`, `zolai/api/session_auth.py`,
`zolai/api/auth.py`) historically wrote space-format session and API-key
expiry and compared those strings as TEXT against stored rows; changing the
writer without also rewriting the stored rows would have made every existing
session read as expired. It is closed: writers go through `clock.stamp()`,
readers use `clock.parse()` for aware-datetime compare (which is format
tolerant, so any legacy space row still resolves correctly), and the
`AUTH_LANE` exempt set in the test is empty. The scan is uniform across the
tree.

As of 2026-10-29 the live database matches the rule as well: every column
`DEFAULT` is `clock.SQL_DEFAULT` (84 rebuilt across 50 tables) and every
mixed-format column has been normalised. Ledgers for both passes are under
`data/backups/` (`ddl_default_rebuild.jsonl`, `timestamp_normalization.jsonl`)
and both scripts support a revert. 74 columns still hold a single legacy
space format because no canonical writer ever touched them; they order
correctly among themselves, and the first canonical insert into one turns it
mixed — run `scripts/normalize_timestamps.py --apply` after such a change.
