#!/usr/bin/env python3
"""Knowledge-quality evaluation harness — deterministic, SQL-only, no LLM.

Measures a fixed metric suite (spec ``spec-knowledge-eval.md`` §3), enforces
byte-exact regression pins (§4), structural guards (§4.3) and per-track
done-when registries (§4.4) against the canonical Zolai knowledge DB, and emits
a JSON + markdown report (spec §5.3).

Design rules (all enforced by ``tests/test_knowledge_quality_report.py``):

* **Read-only by construction.** The default path opens sqlite with
  ``file:<path>?mode=ro`` (URI) + ``PRAGMA query_only`` and never constructs a
  rw connection. ``--persist`` is the only opt-in write path, and it writes
  only to the additive ``knowledge_quality_runs`` / ``knowledge_quality_metrics``
  tables (ADR-015), with ``--yes`` (or an interactive confirmation) required.
* **No thresholds in this file.** Every amber/green/baseline value, every SQL
  string, pin and track check lives in ``knowledge_quality_pins.json`` — the
  single source for the whole suite.
* **Exit codes:** 0 green/grandfathered · 1 red · 2 amber · 3 harness error
  (DB unreachable, sqlite < 3.30, non-ro URI, bad pins/track).
* **Portability:** GLOB unicode ranges need sqlite >= 3.30 (verified on 3.53.4).

Usage::

    knowledge_quality_report.py                     # default read-only full run
    knowledge_quality_report.py --track R2-C        # one track's done-when
    knowledge_quality_report.py --compare prev.json # delta + R/Y/G transitions
    knowledge_quality_report.py --deep              # + M6c writer scan, M13c anti-join
    knowledge_quality_report.py --persist --yes     # owner-gated run persistence
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_PINS = SCRIPT_DIR / "knowledge_quality_pins.json"
DEFAULT_OUT = REPO_ROOT / "report" / "kq"
SQLITE_MIN_VERSION = (3, 30)

#: Shared predicates (single source — pins.json embeds the same strings).
BURMESE_GLOB = "*[က-႟]*"


def ref_conformance_sql(col: str) -> str:
    """``BOOK CH:V`` conformance predicate (spec §3.7) for a reference column."""
    return (
        f"{col} GLOB '* *:*' AND {col} NOT GLOB '*.*' AND {col} NOT GLOB '*[^A-Z0-9: ]*'"
        f" AND {col} NOT LIKE '%  %'"
    )


def burmese_sql(col: str) -> str:
    """Burmese-script contamination predicate (spec §3.5) for a text column."""
    return f"{col} GLOB '{BURMESE_GLOB}'"


def book_code_sql(col: str) -> str:
    """Genuine Bible book-code predicate (spec §3.7 M9e)."""
    return (
        f"length({col}) BETWEEN 3 AND 4 AND {col} GLOB '*[A-Z]*' AND {col} NOT GLOB '*[^A-Z0-9]*'"
    )


class HarnessError(Exception):
    """Fatal harness condition → exit code 3."""

    def __init__(self, message: str, code: int = 3) -> None:
        super().__init__(message)
        self.code = code


# ── status vocabulary ──────────────────────────────────────────────────────

#: statuses that never fail a run (reported, but exit-neutral)
NEUTRAL_STATUSES = {"info", "absent", "skipped", "manual", "dropped"}
STATUS_RANK = {"green": 0, "info": 0, "absent": 0, "skipped": 0, "manual": 0,
               "dropped": 0, "amber": 1, "red": 2}


def threshold_status(metric: dict, value: float | int | None) -> str:
    """Map a measured value to green/amber/red using pins.json thresholds.

    * ``direction="lower"`` — smaller is better (dups, junk, empties)
    * ``direction="higher"`` — larger is better (coverage, conformance)
    * ``direction="info"``   — reported only, never fails
    * ``amber=None``         — threshold amber band extends to infinity: worse
      than green is amber, never red (the structural red is the G-* guard)
    * ``value=None``         — metric absent (missing/empty table) — neutral
    """
    direction = metric.get("direction") or "lower"
    if direction == "info":
        return "info"
    if value is None:
        return "absent"
    amber = metric.get("amber")
    green = metric.get("green")
    if direction == "higher":
        if green is not None and value >= green:
            return "green"
        if amber is None:
            return "amber"
        return "amber" if value >= amber else "red"
    if green is not None and value <= green:
        return "green"
    if amber is None:
        return "amber"
    return "amber" if value <= amber else "red"


def worst_status(statuses: list[str], *, ignore_neutral: bool = False) -> str:
    best = "green"
    for s in statuses:
        if ignore_neutral and s in NEUTRAL_STATUSES:
            continue
        if STATUS_RANK.get(s, 0) > STATUS_RANK.get(best, 0):
            best = s
    if ignore_neutral and best == "green" and all(
        s in NEUTRAL_STATUSES for s in statuses if s != "green"
    ):
        return "green"
    return best


# ── pins / db resolution ───────────────────────────────────────────────────


def load_pins(path: Path) -> dict:
    if not path.exists():
        raise HarnessError(f"pins file not found: {path}")
    try:
        pins = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"pins file is not valid JSON: {path}: {exc}") from exc
    for key in ("metrics", "pins", "guards", "tracks"):
        if key not in pins:
            raise HarnessError(f"pins file missing '{key}': {path}")
    return pins


def resolve_db(cli_db: str | None) -> Path:
    """Single resolver: ``config.paths.db``, which already honours
    ``ZOLAI_DB_PATH`` and ``ZOLAI_DATA_ROOT``.

    This used to read those variables by hand before trying the package, so the
    report could answer a different question than the toolkit about which
    database it had just scored.
    """
    if cli_db:
        return Path(cli_db).expanduser()
    try:  # pragma: no cover — heavy package init is optional here
        from zolai.config import config  # type: ignore[import-not-found]

        return Path(config.paths.db)
    except Exception:
        return REPO_ROOT.parent / "data" / "zolai.db"


def connect_ro(db_arg: str) -> sqlite3.Connection:
    """Open the DB strictly read-only; refuse any non-ro URI (exit 3)."""
    raw = str(db_arg)
    if raw.startswith("file:"):
        query = raw.split("?", 1)[1] if "?" in raw else ""
        modes = [p for p in query.split("&") if p.startswith("mode=")]
        if modes and modes[0] != "mode=ro":
            raise HarnessError(f"refusing non-readonly URI (mode must be ro): {raw}")
        uri = raw if "mode=ro" in raw else f"{raw}{'&' if query else '?'}mode=ro"
        path = Path(raw[5:].split("?", 1)[0])
    else:
        path = Path(raw)
        uri = f"file:{path.resolve()}?mode=ro"
    if not path.exists():
        raise HarnessError(f"database not found: {path}")
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        raise HarnessError(f"cannot open database read-only: {path}: {exc}") from exc
    if tuple(sqlite3.sqlite_version_info[:2]) < SQLITE_MIN_VERSION:
        raise HarnessError(
            f"sqlite {sqlite3.sqlite_version} < {'.'.join(map(str, SQLITE_MIN_VERSION))} "
            "— GLOB unicode ranges are required"
        )
    return conn


def git_rev() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    """First column of the first row; ``None`` for a missing *table*.

    A missing *column* is a pins/SQL defect and raises (harness error), so a
    silent schema drift can never read as a healthy metric.
    """
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise HarnessError(f"SQL failed: {exc}\n  sql: {sql}") from exc
    return row[0] if row else None


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
    )


# ── processors (metrics that need more than one SQL) ───────────────────────


def proc_empty_tables(conn: sqlite3.Connection, metric: dict, ctx: dict) -> dict:
    params = metric.get("params", {})
    exclude = tuple(params.get("exclude_prefixes", ("sqlite_", "%_fts")))
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    empty: list[str] = []
    for name in names:
        if name.startswith(exclude[0]) or any(pat.strip("%") in name for pat in exclude[1:]):
            continue
        count = scalar(conn, f'SELECT COUNT(*) FROM "{name}"')
        if count == 0:
            empty.append(name)
    return {"value": len(empty), "detail": {"empty_tables": empty}}


def proc_import_reconciliation(conn: sqlite3.Connection, metric: dict, ctx: dict) -> dict:
    mismatch: list[list] = []
    for table_name, claimed in conn.execute(
        "SELECT table_name, SUM(rows_imported) FROM import_log WHERE status='completed'"
        " GROUP BY table_name"
    ):
        if not table_exists(conn, table_name):
            mismatch.append([table_name, claimed, None, None, "NO_TABLE"])
            continue
        live = scalar(conn, f'SELECT COUNT(*) FROM "{table_name}"')
        if live != claimed:
            mismatch.append([table_name, claimed, live, (live or 0) - (claimed or 0)])
    return {"value": len(mismatch), "detail": {"mismatches": mismatch}}


def parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


_GATE_CACHE: dict[str, bool] = {}


def proc_eval_health(conn: sqlite3.Connection, metric: dict, ctx: dict) -> dict:
    params = metric.get("params", {})
    last = scalar(conn, "SELECT MAX(created_at) FROM eval_runs")
    stamp = parse_ts(last)
    days = None if stamp is None else max(0, int((ctx["now"] - stamp).total_seconds() // 86400))
    cmd_src = params.get("gate_import_cmd")
    gate_ok = None
    if cmd_src:
        key = str(cmd_src)
        if key not in _GATE_CACHE:
            timeout = int(params.get("gate_timeout_s", 90))
            env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", key], cwd=str(REPO_ROOT), env=env,
                    capture_output=True, text=True, timeout=timeout,
                )
                _GATE_CACHE[key] = proc.returncode == 0
            except Exception:
                _GATE_CACHE[key] = False
        gate_ok = _GATE_CACHE[key]
    return {
        "value": days,
        "sub_values": {"gate_importable": 0 if gate_ok else 1},
        "detail": {
            "eval_runs_last": last,
            "gate_importable": gate_ok,
            "corpus_reports": scalar(conn, "SELECT COUNT(*) FROM corpus_quality_reports"),
            "corpus_last": scalar(conn, "SELECT MAX(created_at) FROM corpus_quality_reports"),
        },
    }


def proc_verse_union_gap(conn: sqlite3.Connection, metric: dict, ctx: dict) -> dict:
    import_only = scalar(
        conn,
        "SELECT COUNT(DISTINCT ref) FROM bible_verses_import i"
        " WHERE NOT EXISTS (SELECT 1 FROM bible_verses b WHERE b.ref=i.ref)",
    )
    main_only = scalar(
        conn,
        "SELECT COUNT(DISTINCT ref) FROM bible_verses b"
        " WHERE NOT EXISTS (SELECT 1 FROM bible_verses_import i WHERE i.ref=b.ref)",
    )
    return {
        "value": import_only,
        "sub_values": {"main_only": main_only},
        "detail": {"import_only": import_only, "main_only": main_only},
    }


_WRITER_RE = re.compile(r"\b(insert\s+into|create\s+table|update|delete\s+from)\s+[`\"']?{name}\b", re.I)


def proc_writer_scan(conn: sqlite3.Connection, metric: dict, ctx: dict) -> dict:
    params = metric.get("params", {})
    roots = params.get("roots", ["scripts", "."])
    haystacks: list[str] = []
    scanned = 0
    for root in roots:
        base = (REPO_ROOT / root).resolve() if not str(root).startswith("/") else Path(root)
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            try:
                haystacks.append(path.read_text(encoding="utf-8", errors="ignore").lower())
                scanned += 1
            except OSError:
                continue
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    orphans = []
    joined = "\n".join(haystacks)
    for table in tables:
        if not _WRITER_RE.format(name=re.escape(table.lower())).search(joined):
            orphans.append(table)
    return {"value": len(orphans), "detail": {"orphan_tables": orphans, "files_scanned": scanned}}


PROCESSORS = {
    "empty_tables": proc_empty_tables,
    "import_reconciliation": proc_import_reconciliation,
    "eval_health": proc_eval_health,
    "verse_union_gap": proc_verse_union_gap,
    "writer_scan": proc_writer_scan,
}


# ── metric evaluation ──────────────────────────────────────────────────────


def evaluate_metric(conn: sqlite3.Connection, metric: dict, *, deep: bool, ctx: dict) -> dict:
    result: dict[str, Any] = {
        "id": metric["id"],
        "label": metric.get("label", metric["id"]),
        "family": metric.get("family"),
        "track": metric.get("track"),
        "measure": metric.get("measure", "count"),
        "direction": metric.get("direction", "lower"),
        "baseline": metric.get("baseline"),
        "amber": metric.get("amber"),
        "green": metric.get("green"),
        "sql": metric.get("sql") or metric.get("processor"),
        "value": None,
        "denominator": None,
        "ratio": None,
        "measured": None,
        "status": "absent",
        "detail": {},
        "note": metric.get("note"),
        "deep": bool(metric.get("deep")),
    }
    if metric.get("deep") and not deep:
        result["status"] = "skipped"
        return result

    kind = metric.get("kind", "sql")
    if kind == "sql":
        result["value"] = scalar(conn, metric["sql"])
        den_sql = metric.get("denominator_sql")
        if den_sql:
            result["denominator"] = scalar(conn, den_sql)
    elif kind == "sql_map":
        detail: dict[str, Any] = {}
        values: list[int] = []
        for label, sql in metric.get("sqls", {}).items():
            got = scalar(conn, sql)
            detail[label] = got
            if got is None:
                detail[f"{label}__absent"] = True
            else:
                values.append(got)
        reduce_mode = metric.get("reduce", "sum")
        if not values:
            result["value"] = None
        elif reduce_mode == "sum":
            result["value"] = sum(values)
        elif reduce_mode == "max":
            result["value"] = max(values)
        elif reduce_mode == "min":
            result["value"] = min(values)
        elif reduce_mode == "zero_count":
            result["value"] = sum(1 for v in values if v == 0)
        elif reduce_mode == "nonzero_count":
            result["value"] = sum(1 for v in values if v != 0)
        else:
            raise HarnessError(f"unknown sql_map reduce '{reduce_mode}' for {metric['id']}")
        result["detail"] = detail
        den_sql = metric.get("denominator_sql")
        if den_sql:
            result["denominator"] = scalar(conn, den_sql)
    elif kind == "ratio":
        if _evaluate_ratio(conn, metric, result, metric["table"], metric["key_expr"],
                           dropped_ok=bool(metric.get("dropped_ok"))):
            return result  # dropped/absent — status already final, do not re-threshold
    elif kind == "ratio_multi":
        parts_detail: dict[str, Any] = {}
        ratios: list[float] = []
        for part in metric.get("parts", []):
            key = part["table"]
            got = _ratio_for(conn, key, part["key_expr"])
            parts_detail[key] = got
            if got is not None:
                ratios.append(got)
        result["value"] = round(max(ratios), 4) if ratios else None
        result["measured"] = result["value"]
        result["detail"] = parts_detail
    elif kind == "processor":
        fn = PROCESSORS.get(metric.get("processor", ""))
        if fn is None:
            raise HarnessError(f"unknown processor '{metric.get('processor')}' for {metric['id']}")
        produced = fn(conn, metric, ctx)
        result["value"] = produced.get("value")
        result["denominator"] = produced.get("denominator")
        result["detail"] = produced.get("detail", {})
        if "sub_values" in produced:
            result["sub_values"] = produced["sub_values"]
    else:
        raise HarnessError(f"unknown metric kind '{kind}' for {metric['id']}")

    # explicit per-metric detail SQL (reporting only, never thresholded)
    for label, sql in metric.get("detail_sqls", {}).items():
        result["detail"][label] = scalar(conn, sql)

    measure = result["measure"]
    if measure == "pct":
        den = result["denominator"]
        if result["value"] is None or not den:
            result["status"] = "absent"
            return result
        result["ratio"] = result["value"] / den
        result["measured"] = round(result["ratio"] * 100, 2)
    elif measure == "dup":
        result["measured"] = result["value"]
    else:
        result["measured"] = result["value"]

    status = threshold_status(metric, result["measured"])

    sub_checks = []
    for sub in metric.get("sub_thresholds", []):
        sub_values = result.get("sub_values", {})
        if sub["label"] not in sub_values:
            continue
        sub_status = threshold_status(sub, sub_values[sub["label"]])
        sub_checks.append({"label": sub["label"], "value": sub_values[sub["label"]],
                           "status": sub_status, "amber": sub.get("amber"),
                           "green": sub.get("green")})
        if STATUS_RANK.get(sub_status, 0) > STATUS_RANK.get(status, 0):
            status = sub_status
    if sub_checks:
        result["sub_checks"] = sub_checks
    result["status"] = status
    return result


def _ratio_for(conn: sqlite3.Connection, table: str, key_expr: str) -> float | None:
    if not table_exists(conn, table):
        return None
    rows, distinct = conn.execute(
        f'SELECT COUNT(*), COUNT(DISTINCT {key_expr}) FROM "{table}"'
    ).fetchone()
    if not rows or not distinct:
        return None
    return round(rows / distinct, 4)


def _evaluate_ratio(conn, metric, result, table, key_expr, *, dropped_ok: bool) -> bool:
    """Fill a dup-ratio result. Returns True when the status is final (caller must not
    re-threshold) — i.e. the source table is gone or holds no measurable rows."""
    result["dropped"] = bool(dropped_ok)
    if not table_exists(conn, table):
        result["status"] = "green" if dropped_ok else "absent"
        result["detail"] = {"table": table, "present": False}
        return True
    rows, distinct = conn.execute(
        f'SELECT COUNT(*), COUNT(DISTINCT {key_expr}) FROM "{table}"'
    ).fetchone()
    result["rows"] = rows
    result["distinct"] = distinct
    result["detail"] = {"table": table, "rows": rows, "distinct": distinct}
    if not rows or not distinct:
        # Empty == no live rows left, so the table is as retired as a dropped one:
        # honour dropped_ok here too rather than falling through to `absent`, which
        # would mask a legitimate reviewer-visible green.
        result["status"] = "green" if dropped_ok else "absent"
        return True
    result["value"] = round(rows / distinct, 4)
    result["measured"] = result["value"]
    result["status"] = threshold_status(metric, result["measured"])
    return False


# ── pins / guards / track checks ───────────────────────────────────────────


def evaluate_pins(conn: sqlite3.Connection, pins: dict, *, enforcing_track: str | None) -> list[dict]:
    out: list[dict] = []
    for pin in pins["pins"]:
        entry: dict[str, Any] = {
            "id": pin["id"],
            "headword": pin.get("headword"),
            "kind": pin.get("kind", "conflict"),
            "mode": pin.get("mode"),
            "active_from": pin.get("active_from"),
            "note": pin.get("note"),
            "status": "green",
            "grandfathered": False,
            "current": None,
            "expected": None,
            "target_met": None,
            "detail": {},
        }
        if pin.get("ambiguous"):
            entry["status"] = "skipped"
            entry["detail"] = {"reason": "ambiguous headword — never enforced (spec §4.2)"}
            out.append(entry)
            continue

        currents, missing = [], False
        for spec in pin.get("enforce", []):
            sql = f'SELECT "{spec["column"]}" FROM "{spec["table"]}" WHERE "{spec["headword_col"]}"=?'
            rows = [r[0] for r in conn.execute(sql, (pin["headword"],)).fetchall()]
            if not rows:
                missing = True
                currents.append(None)
            elif len(rows) == 1:
                currents.append(rows[0])
            else:
                currents.append(sorted(str(r) for r in rows))
        entry["current"] = currents[0] if len(currents) == 1 else currents
        entry["expected"] = (
            pin["enforce"][0]["expected"] if len(pin.get("enforce", [])) == 1
            else [e["expected"] for e in pin.get("enforce", [])]
        )

        context_now = []
        for spec in pin.get("context", []):
            sql = f'SELECT "{spec["column"]}" FROM "{spec["table"]}" WHERE "{spec["headword_col"]}"=?'
            rows = [r[0] for r in conn.execute(sql, (pin["headword"],)).fetchall()]
            context_now.append(rows[0] if len(rows) == 1 else (sorted(str(r) for r in rows) if rows else None))
        entry["detail"]["context_current"] = context_now

        mode = pin.get("mode")
        target_met = None
        if pin.get("target_sql"):
            target_met = scalar(conn, pin["target_sql"]) == 1
            entry["target_met"] = target_met

        expected_values = [e["expected"] for e in pin.get("enforce", [])]
        matched = (not missing) and currents == expected_values

        if mode == "MUST_NOT_REGRESS":
            if matched:
                entry["status"] = "green"
            else:
                entry["status"] = "red"
                entry["detail"]["drift"] = [
                    {"column": f'{e["table"]}.{e["column"]}', "expected": e["expected"],
                     "current": currents[i] if i < len(currents) else None}
                    for i, e in enumerate(pin.get("enforce", []))
                    if i >= len(currents) or currents[i] != e["expected"]
                ]
                if missing:
                    entry["detail"]["row_missing"] = True
        elif mode == "MUST_IMPROVE":
            if target_met:
                entry["status"] = "green"
            elif missing:
                entry["status"] = "red"
                entry["detail"]["reason"] = "pinned row deleted"
            elif enforcing_track is not None and pin.get("active_from") == enforcing_track:
                entry["status"] = "red"
                entry["detail"]["reason"] = (
                    f"enforcing done-when for {enforcing_track}: target_predicate unmet"
                )
            elif matched:
                entry["status"] = "amber"
                entry["grandfathered"] = True
                entry["detail"]["reason"] = (
                    "expected-red: pinned bad value unchanged until "
                    f"{pin.get('active_from')} lands (spec §4)"
                )
            else:
                entry["status"] = "red"
                entry["detail"]["reason"] = (
                    "value changed without meeting target_predicate — complete the track"
                    " or commit an evidenced pins.json update"
                )
        else:
            raise HarnessError(f"unknown pin mode '{mode}' for {pin['id']}")
        out.append(entry)
    return out


def evaluate_guards(conn: sqlite3.Connection, pins: dict) -> list[dict]:
    out: list[dict] = []
    for guard in pins["guards"]:
        parts = []
        for part in guard["parts"]:
            actual = scalar(conn, part["sql"])
            if actual is None:
                status = "absent"
            else:
                op = part["op"]
                want = part["value"]
                ok = {"<=": actual <= want, ">=": actual >= want, "==": actual == want}.get(op)
                if ok is None:
                    raise HarnessError(f"unknown guard op '{op}' in {guard['id']}")
                status = "green" if ok else "red"
            parts.append({"label": part["label"], "op": part["op"], "value": part["value"],
                          "actual": actual, "status": status})
        out.append({
            "id": guard["id"],
            "label": guard.get("label"),
            "status": worst_status([p["status"] for p in parts]),
            "parts": parts,
            "note": guard.get("note"),
        })
    return out


def evaluate_track_checks(
    conn: sqlite3.Connection,
    pins: dict,
    track: str,
    metrics_by_id: dict,
    pins_by_id: dict,
    guards_by_id: dict,
    *,
    deep: bool,
    db_path: Path,
    compare: dict | None,
) -> list[dict]:
    checks = []
    for check in pins["tracks"][track]:
        kind = check["kind"]
        status, actual, detail = "green", None, {}
        if kind == "metric":
            metric = metrics_by_id.get(check["metric"])
            if metric is None:
                status, detail = "skipped", {"reason": f"metric {check['metric']} not computed"}
            elif metric["status"] == "skipped":
                status, detail = "skipped", {"reason": "deep-only metric (run with --deep)"}
            elif metric["status"] == "absent":
                status, detail = "absent", {"reason": "metric table absent"}
            elif "against" in check:
                actual = metric["status"]
                status = "green" if metric["status"] in ("green", "dropped") else "red"
            else:
                actual = metric["measured"]
                if actual is None:
                    status, detail = "skipped", {"reason": "metric has no value"}
                else:
                    op, want = check["op"], check["value"]
                    ok = {"<=": actual <= want, ">=": actual >= want, "==": actual == want}.get(op)
                    if ok is None:
                        raise HarnessError(f"unknown check op '{op}' in {check['id']}")
                    status = "green" if ok else "red"
                    detail = {"op": op, "expected": want, "measured": actual}
        elif kind == "metric_sub":
            metric = metrics_by_id.get(check["metric"])
            sub = None
            if metric is not None:
                sub = next(
                    (s for s in metric.get("sub_checks", []) if s["label"] == check["sub"]), None
                )
            if metric is not None and metric["status"] == "skipped":
                status, detail = "skipped", {"reason": "deep-only metric (run with --deep)"}
            elif sub is None:
                status, detail = "skipped", {"reason": "sub-check not evaluated"}
            else:
                actual = sub["value"]
                op, want = check["op"], check["value"]
                ok = {"<=": actual <= want, ">=": actual >= want, "==": actual == want}.get(op)
                status = "green" if ok else "red"
                detail = {"op": op, "expected": want, "actual": actual}
        elif kind == "guard":
            guard = guards_by_id.get(check["guard"])
            if guard is None:
                status, detail = "skipped", {"reason": "guard not found"}
            else:
                status, actual = guard["status"], guard["status"]
                detail = {"failing": [p["label"] for p in guard["parts"] if p["status"] == "red"]}
        elif kind == "pin":
            pin = pins_by_id.get(check["pin"])
            if pin is None:
                status, detail = "skipped", {"reason": "pin not found"}
            else:
                status, actual = pin["status"], pin["status"]
                detail = {"mode": pin["mode"], "grandfathered": pin["grandfathered"]}
        elif kind == "sql":
            actual = scalar(conn, check["sql"])
            if actual is None:
                status, detail = "skipped", {"reason": "referenced table absent"}
            else:
                op, want = check["op"], check["value"]
                ok = {"<=": actual <= want, ">=": actual >= want, "==": actual == want}.get(op)
                if ok is None:
                    raise HarnessError(f"unknown check op '{op}' in {check['id']}")
                status = "green" if ok else "red"
                detail = {"op": op, "expected": want, "actual": actual}
        elif kind == "table_absent":
            present = table_exists(conn, check["table"])
            status = "red" if present else "green"
            actual = {"table_present": present}
        elif kind == "file_freshness":
            path = _resolve_path(check.get("path_key"))
            if path is None or not path.exists():
                status, detail = "red", {"reason": f"artifact missing: {path}"}
            else:
                age_days = (db_path.stat().st_mtime - path.stat().st_mtime) / 86400.0
                actual = round(age_days, 2)
                status = "green" if age_days <= check.get("max_days", 7) else "red"
                detail = {"artifact": str(path), "max_days": check.get("max_days", 7),
                          "age_days_vs_db": actual}
        elif kind == "compare_regression":
            if compare is None:
                status, detail = "skipped", {"reason": "no --compare baseline supplied"}
            else:
                regs = compare.get("regressions", [])
                status = "green" if not regs else "red"
                actual = len(regs)
                detail = {"regressions": regs}
        elif kind == "manual":
            status, actual = "manual", None
            detail = {"note": check.get("note")}
        else:
            raise HarnessError(f"unknown check kind '{kind}' in {check['id']}")
        checks.append({"track": track, "check_id": check["id"], "kind": kind,
                       "status": status, "actual": actual, "detail": detail,
                       "note": check.get("note")})
    return checks


def _resolve_path(key: str | None) -> Path | None:
    pins_paths = _RESOLVER_PINS.get("paths", {}) if key else {}
    raw = pins_paths.get(key or "")
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    for base in (REPO_ROOT, REPO_ROOT.parent):
        if (base / candidate).exists():
            return base / candidate
    return REPO_ROOT / candidate


_RESOLVER_PINS: dict = {}


# ── report assembly ────────────────────────────────────────────────────────


def compare_reports(prev: dict, current: dict) -> dict:
    transitions, regressions = [], []
    prev_metrics = {m["id"]: m for m in prev.get("metrics", [])}
    for metric in current.get("metrics", []):
        old = prev_metrics.get(metric["id"])
        if not old:
            continue
        if old["status"] == metric["status"]:
            continue
        direction = f"{old['status']}->{metric['status']}"
        delta = None
        if isinstance(old.get("measured"), (int, float)) and isinstance(
            metric.get("measured"), (int, float)
        ):
            delta = round(metric["measured"] - old["measured"], 4)
        item = {"metric": metric["id"], "from_status": old["status"],
                "to_status": metric["status"], "direction": direction, "delta": delta}
        transitions.append(item)
        if old["status"] == "green" and metric["status"] == "red":
            regressions.append(metric["id"])
    pin_transitions = []
    prev_pins = {p["id"]: p for p in prev.get("pins", [])}
    for pin in current.get("pins", []):
        old = prev_pins.get(pin["id"])
        if old and old["status"] != pin["status"]:
            pin_transitions.append(
                {"pin": pin["id"], "from_status": old["status"], "to_status": pin["status"],
                 "direction": f"{old['status']}->{pin['status']}"}
            )
    return {"previous_run_id": prev.get("run_id"), "transitions": transitions,
            "pin_transitions": pin_transitions, "regressions": regressions,
            "new_metrics": [m["id"] for m in current.get("metrics", [])
                            if m["id"] not in prev_metrics]}


def summarize(report: dict) -> tuple[str, int, dict]:
    greens = ambers = reds = grandfathered = 0
    buckets = (
        [(m["status"], False) for m in report.get("metrics", [])]
        + [(p["status"], p.get("grandfathered", False)) for p in report.get("pins", [])]
        + [(g["status"], False) for g in report.get("guards", [])]
        + [(c["status"], False) for c in report.get("track_checks", [])]
    )
    for status, is_grandfathered in buckets:
        if status == "green":
            greens += 1
        elif status == "amber":
            ambers += 1
            if is_grandfathered:
                grandfathered += 1
        elif status == "red":
            reds += 1
    if reds:
        status, exit_code = "red", 1
    elif ambers > grandfathered:
        status, exit_code = "amber", 2
    else:
        status, exit_code = "green", 0
    counts = {"greens": greens, "ambers": ambers, "reds": reds,
              "grandfathered": grandfathered,
              "neutral": len(buckets) - greens - ambers - reds}
    return status, exit_code, counts


def render_md(report: dict) -> str:
    lines = [
        f"# Knowledge-quality report — {report['run_id']}",
        "",
        f"- **status:** {report['status'].upper()} (exit {report['exit_code']}) — "
        f"{report['counts']['greens']} green / {report['counts']['ambers']} amber "
        f"({report['counts']['grandfathered']} grandfathered) / {report['counts']['reds']} red",
        f"- db: `{report['db_path']}` · {report['db_size']:,} B · sqlite {report['sqlite_version']}"
        f" · git {report.get('git_rev') or 'n/a'}",
        f"- started {report['started_at']} · db unixepoch {report.get('db_unixepoch')}"
        f" · deep={report['deep']} · {report['duration_ms']:.0f} ms · pins `{report['pins_file']}`",
        f"- lane: **data quality** (SQL over stored facts) — model behaviour stays in the"
        f" eval lane (M12: gate {'importable' if report.get('gate_importable') else 'NOT importable'});",
        "",
        "## Metrics",
        "",
        "| id | family | metric | baseline | current | Δ | status |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for m in report["metrics"]:
        baseline = m.get("baseline")
        measured = m.get("measured")
        delta = ""
        if isinstance(baseline, (int, float)) and isinstance(measured, (int, float)):
            delta = f"{measured - baseline:+g}"
        lines.append(
            f"| {m['id']} | {m.get('family')} | {m['label']} | {baseline} "
            f"| {'' if measured is None else measured} | {delta} | {m['status']} |"
        )
    lines += ["", "## Regression pins", "",
              "| pin | headword | mode | expected | current | status |",
              "|---|---|---|---|---|---|"]
    for p in report["pins"]:
        lines.append(
            f"| {p['id']} | {p['headword']} | {p['mode']} | {json.dumps(p['expected'], ensure_ascii=False)}"
            f" | {json.dumps(p['current'], ensure_ascii=False)} | {p['status']}"
            f"{' (grandfathered)' if p['grandfathered'] else ''} |"
        )
    lines += ["", "## Structural guards", "",
              "| guard | status | failing parts |", "|---|---|---|"]
    for g in report["guards"]:
        failing = ", ".join(x["label"] for x in g["parts"] if x["status"] == "red") or "—"
        lines.append(f"| {g['id']} | {g['status']} | {failing} |")
    if report.get("track_checks"):
        lines += ["", f"## Track done-when — {report['track_checks'][0]['track']}", "",
                  "| check | kind | status | actual |", "|---|---|---|---|"]
        for c in report["track_checks"]:
            lines.append(f"| {c['check_id']} | {c['kind']} | {c['status']} | {c['actual']} |")
    if report.get("compare"):
        cmp = report["compare"]
        lines += ["", f"## Compare vs {cmp['previous_run_id']}", "",
                  "| metric | from | to | Δ |", "|---|---|---|---:|"]
        for t in cmp["transitions"]:
            lines.append(f"| {t['metric']} | {t['from_status']} | {t['to_status']} | {t['delta']} |")
        if cmp["regressions"]:
            lines += ["", f"**REGRESSIONS (green→red):** {', '.join(cmp['regressions'])}"]
    lines += ["", "## Notes", "",
              "- R/Y/G thresholds come from `scripts/maintenance/knowledge_quality_pins.json` —",
              "  changing a threshold or pin is a reviewed commit citing track + evidence.",
              "- `dropped` = the source table was removed by its track (reviewer-visible green);",
              "  `absent` = table not found and not declared droppable (reported, exit-neutral).",
              "- exit 1 means a red metric/guard/pin/track-check exists; on a pre-R2 database",
              "  that is the honest distance-to-target, not a harness fault.",
              ""]
    return "\n".join(lines)


# ── persistence (opt-in only) ──────────────────────────────────────────────

PERSIST_DDL = [
    """CREATE TABLE IF NOT EXISTS knowledge_quality_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        duration_ms REAL NOT NULL DEFAULT 0,
        db_size_bytes INTEGER,
        git_rev TEXT,
        deep INTEGER NOT NULL DEFAULT 0,
        metrics_total INTEGER,
        greens INTEGER,
        ambers INTEGER,
        reds INTEGER,
        status TEXT NOT NULL,
        report_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS knowledge_quality_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES knowledge_quality_runs(id),
        metric_id TEXT NOT NULL,
        value REAL,
        denominator REAL,
        ratio REAL,
        status TEXT NOT NULL,
        baseline REAL,
        amber REAL,
        green REAL,
        UNIQUE(run_id, metric_id)
    )""",
]


def persist_run(db_path: Path, report: dict, *, assume_yes: bool) -> int:
    """Short-lived rw connection — only ever reached with --persist + confirmation."""
    if not assume_yes:
        if not sys.stdin.isatty():
            raise HarnessError("--persist needs an interactive confirmation or --yes")
        answer = input(f"Write KQ run {report['run_id']} into {db_path}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            raise HarnessError("persist aborted by operator")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout=60000")
        with conn:
            for ddl in PERSIST_DDL:
                conn.execute(ddl)
            cur = conn.execute(
                "INSERT INTO knowledge_quality_runs (started_at, duration_ms, db_size_bytes,"
                " git_rev, deep, metrics_total, greens, ambers, reds, status, report_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report["started_at"],
                    report["duration_ms"],
                    report["db_size"],
                    report.get("git_rev"),
                    1 if report["deep"] else 0,
                    len(report["metrics"]),
                    report["counts"]["greens"],
                    report["counts"]["ambers"],
                    report["counts"]["reds"],
                    report["status"],
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            run_id = cur.lastrowid
            for m in report["metrics"]:
                conn.execute(
                    "INSERT INTO knowledge_quality_metrics (run_id, metric_id, value,"
                    " denominator, ratio, status, baseline, amber, green) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, m["id"],
                        _num(m.get("measured")), _num(m.get("denominator")), _num(m.get("ratio")),
                        m["status"], _num(m.get("baseline")), _num(m.get("amber")),
                        _num(m.get("green")),
                    ),
                )
    finally:
        conn.close()
    return int(run_id)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ── CLI ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge_quality_report.py",
        description="Deterministic knowledge-quality evaluation harness (read-only by default).",
    )
    parser.add_argument("--db", default=None, help="database path (default: config.paths.zolai_db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="read-only run (default and implied)")
    parser.add_argument("--persist", action="store_true",
                        help="opt-in: write the run into knowledge_quality_runs/_metrics")
    parser.add_argument("--yes", action="store_true", help="skip the --persist confirmation")
    parser.add_argument("--track", default=None, help="run only this track's done-when checks")
    parser.add_argument("--format", choices=("json", "md", "both"), default="both")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="report output directory")
    parser.add_argument("--deep", action="store_true",
                        help="enable M6c writer scan + M13c verse-union anti-join (minutes)")
    parser.add_argument("--compare", default=None, help="previous report JSON to diff against")
    parser.add_argument("--pins", default=str(DEFAULT_PINS), help="pins/threshold registry JSON")
    return parser


def run(argv: list[str]) -> int:
    started = time.time()
    args = build_parser().parse_args(argv)
    if args.dry_run and args.persist:
        print("[kq] --dry-run and --persist are mutually exclusive", file=sys.stderr)
        return 3

    try:
        pins_path = Path(args.pins)
        pins = load_pins(pins_path)
        _RESOLVER_PINS.clear()
        _RESOLVER_PINS.update(pins)

        if args.track is not None and args.track not in pins["tracks"]:
            raise HarnessError(f"unknown track '{args.track}' — known: {', '.join(pins['tracks'])}")

        compare: dict | None = None
        if args.compare:
            prev_path = Path(args.compare)
            if not prev_path.exists():
                raise HarnessError(f"--compare file not found: {prev_path}")
            compare = compare_reports(json.loads(prev_path.read_text(encoding="utf-8")), {})

        db_arg = args.db or str(resolve_db(None))
        if db_arg.startswith("file:") and "?" in db_arg:
            db_path = Path(db_arg[5:].split("?", 1)[0])
        else:
            db_path = Path(db_arg)
        conn = connect_ro(db_arg)
    except HarnessError as exc:
        print(f"[kq] {exc}", file=sys.stderr)
        return exc.code
    except sqlite3.Error as exc:
        print(f"[kq] sqlite error: {exc}", file=sys.stderr)
        return 3

    now = datetime.now(timezone.utc)
    ctx = {"now": now, "pins": pins, "deep": args.deep}
    started_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = f"kq-{now.strftime('%Y%m%dT%H%M%SZ')}"

    try:
        selected_metrics = list(pins["metrics"])
        if args.track is not None:
            referenced = {
                c["metric"]
                for c in pins["tracks"][args.track]
                if c["kind"] in ("metric", "metric_sub")
            }
            selected_metrics = [m for m in pins["metrics"] if m["id"] in referenced]

        metrics = [evaluate_metric(conn, m, deep=args.deep, ctx=ctx) for m in selected_metrics]
        pin_results = evaluate_pins(conn, pins, enforcing_track=args.track)
        guard_results = evaluate_guards(conn, pins)

        metrics_by_id = {m["id"]: m for m in metrics}
        pins_by_id = {p["id"]: p for p in pin_results}
        guards_by_id = {g["id"]: g for g in guard_results}
        track_checks: list[dict] = []
        if args.track is not None:
            track_checks = evaluate_track_checks(
                conn, pins, args.track, metrics_by_id, pins_by_id, guards_by_id,
                deep=args.deep, db_path=db_path, compare=compare,
            )

        gate_importable = None
        m12 = metrics_by_id.get("M12")
        if m12 is not None:
            gate_importable = m12.get("detail", {}).get("gate_importable")

        report: dict[str, Any] = {
            "run_id": run_id,
            "db_path": str(db_path),
            "db_size": db_path.stat().st_size if db_path.exists() else 0,
            "sqlite_version": sqlite3.sqlite_version,
            "git_rev": git_rev(),
            "started_at": started_at,
            "db_unixepoch": scalar(conn, "SELECT unixepoch()"),
            "pins_file": str(pins_path),
            "track": args.track,
            "deep": bool(args.deep),
            "duration_ms": 0.0,
            "metrics": metrics,
            "pins": pin_results,
            "guards": guard_results,
            "track_checks": track_checks,
            "gate_importable": gate_importable,
        }
        if compare is not None:
            # transitions need the full current metric list — rebuild from this run
            compare = compare_reports(
                json.loads(Path(args.compare).read_text(encoding="utf-8")), report
            )
            report["compare"] = compare

        report["status"], exit_code, report["counts"] = summarize(report)
        report["exit_code"] = exit_code
        report["duration_ms"] = round((time.time() - started) * 1000, 1)

        if args.persist:
            run_row = persist_run(db_path, report, assume_yes=args.yes)
            report["persisted_run_row"] = run_row

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = out_dir / run_id
        if args.format in ("json", "both"):
            stem.with_suffix(".json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        if args.format in ("md", "both"):
            stem.with_suffix(".md").write_text(render_md(report), encoding="utf-8")

        print(
            f"[kq] {run_id} {report['status'].upper()} — "
            f"{report['counts']['greens']}g/{report['counts']['ambers']}a/"
            f"{report['counts']['reds']}r "
            f"(+{report['counts']['grandfathered']} grandfathered pins) "
            f"in {report['duration_ms']:.0f} ms → {stem}.json"
        )
        return exit_code
    except HarnessError as exc:
        print(f"[kq] {exc}", file=sys.stderr)
        return exc.code
    except sqlite3.Error as exc:
        print(f"[kq] sqlite error: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()


def main() -> None:  # pragma: no cover
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    main()
