#!/usr/bin/env python3
"""ZVS 2018 content scanner with regression-gated CI mode.

Scans repository text content (wiki Markdown files and/or corpus JSONL
records) against the ZVS 2018 validator and writes a non-blocking report, and
optionally enforces a **regression gate** against a committed baseline.

This is a **report-only** tool by default:

- It NEVER writes to any source file (wiki / corpus content is read-only).
- It exits ``0`` ALWAYS, even when violations are found — scans are
  informational and non-blocking for CI.
- Only the **contents** of files are scanned. File names and directory names
  are never tokenized (so a file named ``bawipa.md`` cannot trip a rule).

With ``--gate`` it becomes a regression check:

- A baseline file (committed, e.g. ``report/zvs-baseline.json``) is loaded.
  It is a flat dict keyed by ``source\\u0000rule_id\\u0000forbidden`` -> count.
- The gate exits ``1`` **only** when a combo is NEW or its current count
  exceeds the baseline count. Existing counts never fail, so vocabulary /
  generated / stem-mapping reference content is auto-excluded by having been
  captured in the baseline — with zero per-source classification code.
- The gate exits ``0`` when every combo is present at or below its baseline.

A ``reference`` triage bucket is assigned (for the human summary only) to
lexicographic reference content: paths under ``vocabulary/`` /
``vocabulary/generated/``, ``*_auto.md`` files, and the stem-mapping tables.
This bucket is purely cosmetic and is **never** consulted by the gate.

Usage::

    python scripts/zvs/scan_content.py --wiki                 # wiki scan (default on)
    python scripts/zvs/scan_content.py --corpus               # corpus scan (local/on-demand)
    python scripts/zvs/scan_content.py --wiki --corpus        # both
    python scripts/zvs/scan_content.py --wiki --write-baseline report/zvs-baseline.json
    python scripts/zvs/scan_content.py --wiki --gate --baseline report/zvs-baseline.json

Outputs (written to the repo ``report/`` dir):

- ``zvs-scan-<iso-date>.json``  — full machine-readable result.
- ``zvs-scan-summary.md``       — human summary (counts + triage buckets).
- ``zvs-baseline.json``         — committed baseline (only with --write-baseline).

If the wiki checkout is not present (e.g. a fresh zolai-core checkout), a
clear warning is printed and (in default report-only mode) the script still
exits ``0``. In ``--gate`` mode a missing wiki or missing baseline is a hard
``1`` because the regression gate cannot be validated.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterator

from zolai.zvs import Report, validate
from zolai.zvs import rules_data
from zolai.zvs.rules_data import DEFAULT_EXCEPTIONS

# Repo root is two levels up from this file (scripts/zvs/... -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WIKI = REPO_ROOT.parent / "zolai-wiki"
DEFAULT_CORPUS = REPO_ROOT.parent / "data" / "corpus"
DEFAULT_DICT = REPO_ROOT.parent / "data" / "dictionary" / "processed"
REPORT_DIR = REPO_ROOT / "report"
DEFAULT_BASELINE = REPORT_DIR / "zvs-baseline.json"

# The three authoritative (corpus-derived) dictionary JSONL files scanned by the
# ``--dict`` lane. These are the "bible" schema sources, kept in the shared,
# git-ignored ``data/`` container so dictionary data edits are never committed.
DICT_FILES: tuple[str, ...] = (
    "dict_bible_zo_en_v1.jsonl",
    "dict_bible_learned_v1.jsonl",
    "dict_bible_en_zo_v1.jsonl",
)

# Literal forbidden substrings for the fast pre-filter. Any ZVS violation's
# ``forbidden`` token must appear verbatim in the text (rules are literal
# substring regexes), so a text containing NONE of these can never trip a rule.
# This avoids a full regex pass over the ~35 MB dictionary JSONL files.
_FORBIDDEN_LITERALS: tuple[str, ...] = tuple(
    set(rules_data.DIALECT_FORBIDDEN_TO_PREFERRED)
    | set(rules_data.COMPOUND_SPLIT_TO_PREFERRED)
    | set(rules_data.STEM_FORBIDDEN_TO_PREFERRED)
)

# The seven forbidden forms quantified at the head of the dict-lane report.
_DICT_FORM_BREAKDOWN: tuple[str, ...] = (
    "suah",
    "hi leh",
    "na ding",
    "na sep",
    "ram",
    "nunnak",
    "zalenna",
)

# The ``--suah-triage`` candidate set: compound headwords ending in ``suah``
# whose root is a distinct morpheme (NOT the bare ``suah`` verb family). These
# are the lexemes the prior dictionary-first ZVS audit flagged for review.
COMPOUND_SUAH_LEXEMES: tuple[str, ...] = (
    "hansuah",
    "khahsuah",
    "kisuah",
    "kisosuah",
    "maisuah",
    "meidawisuah",
    "nausuah",
    "nisuah",
    "phelsuah",
    "pusuah",
    "sehlisuah",
    "sehsawmsuah",
    "sehthumsuah",
    "siatsuah",
    "sihsuah",
    "sosuah",
    "taisuah",
    "toksuahpa",
)

# Minimum corpus attestations for a compound-suah lexeme to be called LEGIT.
# Below this the corpus is too thin to assert native usage -> UNVERIFIED.
_SUAH_LEGIT_FLOOR = 10

# Field names to read from JSONL records (mirrors the zolai-zvs CLI).
_JSONL_FIELDS = ("text", "zolai", "sentence", "corrected", "original")

# Composite-key separator between source / rule_id / forbidden.
_KEY_SEP = "\u0000"

# Triage buckets used in the summary.
_BUCKET_REFERENCE = "reference"  # lexicographic reference — never gates.
_BUCKET_EXCEPTION = "exception"
_BUCKET_FIX = "fix"
_BUCKET_IGNORE = "ignore"
_BUCKET_FALSE_POSITIVE = "false-positive"
_ALL_BUCKETS = (
    _BUCKET_FIX,
    _BUCKET_REFERENCE,
    _BUCKET_EXCEPTION,
    _BUCKET_IGNORE,
    _BUCKET_FALSE_POSITIVE,
)

# Reference content markers used only for the human summary (never for gating).
_REFERENCE_MARKERS = ("vocabulary/", "_auto.md", "forbidden_stems_auto.md")


def _is_reference_source(source: str) -> bool:
    """True for lexicographic/auto reference sources (cosmetic bucket only).

    Matches vocabulary/, vocabulary/generated/, ``*_auto.md`` files, and the
    auto-joined stem-mapping table ``grammar/forbidden_stems_auto.md``.
    """
    normalized = source.replace("\\", "/")
    for marker in _REFERENCE_MARKERS:
        if marker in normalized:
            return True
    return False


def _triage_bucket(report: Report, v) -> str:
    """Classify a single violation into a triage bucket."""
    # The stem-mapping section of 03_negation_particles.md is a reference
    # table; its STEM_* rows are bucketed as reference (cosmetic only).
    if report.source.replace("\\", "/").endswith(
        "bundle/03_negation_particles.md"
    ) and v.rule_id.startswith("STEM_"):
        return _BUCKET_REFERENCE
    if _is_reference_source(report.source):
        return _BUCKET_REFERENCE
    if v.category == "phonotactic":
        return _BUCKET_FALSE_POSITIVE
    if v.forbidden.strip().lower() in DEFAULT_EXCEPTIONS.tokens:
        return _BUCKET_EXCEPTION
    if v.preferred is None:
        return _BUCKET_IGNORE
    return _BUCKET_FIX


def _rel_source(source: str) -> str:
    """Normalize an absolute scan source to a repo-stable relative path.

    Baseline keys must be portable across machines (local vs CI), so absolute
    source paths are stripped back to the wiki/corpus root.
    """
    normalized = source.replace("\\", "/")
    for root in (DEFAULT_WIKI, DEFAULT_CORPUS):
        prefix = str(root).replace("\\", "/").rstrip("/") + "/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        if normalized == str(root):
            return ""
    return normalized


def _iter_wiki_texts(wiki_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(source, content)`` pairs for every Markdown file in the wiki.

    Only the file **contents** are yielded; names/dirs are never scanned.
    """
    for path in sorted(wiki_dir.rglob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        yield str(path), content


def _iter_corpus_texts(corpus_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(source, text)`` pairs from JSONL records."""
    for path in sorted(corpus_dir.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        for field in _JSONL_FIELDS:
                            value = record.get(field)
                            if isinstance(value, str) and value:
                                source = f"{path}:{lineno}[{field}]"
                                yield source, value
        except OSError:
            continue


def _dict_fields(record: dict) -> Iterator[tuple[str, str]]:
    """Yield ``(location, text)`` Zolai-language strings for a dictionary record.

    Schema-aware extraction across the three bible dictionary sources:

    - ``zo_en`` (``dict_bible_zo_en_v1.jsonl``): ``zolai`` -> headword,
      ``examples[].zo`` -> example, ``variants[]`` -> variant,
      ``usage_notes`` -> usage_note.
    - ``learned`` (``dict_bible_learned_v1.jsonl``): ``zolai`` -> headword
      (its ``translations`` are English glosses and are not validated).
    - ``en_zo`` (``dict_bible_en_zo_v1.jsonl``): ``english`` is the English
      index term (not validated); ``zolai_equivalents[]`` -> equivalent.
    """
    zolai = record.get("zolai")
    if isinstance(zolai, str) and zolai:
        yield "headword", zolai
    for ex in record.get("examples") or ():
        if isinstance(ex, dict):
            zo = ex.get("zo")
            if isinstance(zo, str) and zo:
                yield "example", zo
        elif isinstance(ex, str) and ex:
            yield "example", ex
    for variant in record.get("variants") or ():
        if isinstance(variant, str) and variant:
            yield "variant", variant
    usage = record.get("usage_notes")
    if isinstance(usage, str) and usage:
        yield "usage_note", usage
    for tr in record.get("translations") or ():
        if isinstance(tr, str) and tr:
            yield "translation", tr
    for eq in record.get("zolai_equivalents") or ():
        if isinstance(eq, str) and eq:
            yield "equivalent", eq


def _iter_dict_texts(dict_dir: Path) -> Iterator[tuple[str, str, str]]:
    """Yield ``(source, text, location)`` from the dict JSONL files."""
    for filename in DICT_FILES:
        path = dict_dir / filename
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                for location, text in _dict_fields(record):
                    source = f"{filename}:{lineno}"
                    yield source, text, location


def _fast_has_forbidden(text: str) -> bool:
    """True if the text contains any forbidden literal (fast pre-filter)."""
    lowered = text.lower()
    return any(needle in lowered for needle in _FORBIDDEN_LITERALS)


def _count_compound_suah(dict_dir: Path, corpus_dir: Path) -> dict[str, int]:
    """Count corpus attestations of each compound-suah lexeme (plain-substring).

    Only positive matches count; the corpus files (``unified/bible`` markdown +
    ``corpus_unified_v1.jsonl``) are read in a single streaming pass without
    JSON parsing, so a 750 MB corpus is scanned in ~seconds.
    """
    counts: dict[str, int] = {lexeme: 0 for lexeme in COMPOUND_SUAH_LEXEMES}
    needles = list(counts)
    unicode_lower = {w: w.lower() for w in needles}

    def _count_content(content: str) -> None:
        low = content.lower()
        for lexeme, needle in unicode_lower.items():
            counts[lexeme] += low.count(needle)

    bible_md = corpus_dir / "bible" / "markdown"
    for path in sorted(
        [*bible_md.rglob("*.md"), corpus_dir / "corpus_unified_v1.jsonl"]
    ):
        if not path.is_file():
            continue
        try:
            _count_content(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return counts


def triage_compound_suah(dict_dir: Path, corpus_dir: Path) -> dict[str, str]:
    """Classify each compound-suah lexeme into LEGIT / WRONG / UNVERIFIED.

    Conservative by design: a lexeme is **LEGIT** when it is well-attested as a
    native compound in the corpus (``>= _SUAH_LEGIT_FLOOR`` occurrences of the
    ``suah`` spelling). It is never marked **WRONG** here unless the corpus
    clearly uses the ``chuak``/``suak`` appear/rise form instead — the audits so
    far found no such case, so compound-``suah`` verb lexemes stay as-is. Thinly
    attested lexemes fall through to **UNVERIFIED** for owner vetting.

    Only the lexeme surface is bucketed; the bare ``suah`` verb family is out of
    scope (the eval-fixture piangsak/chuak corrections were already handled).
    """
    counts = _count_compound_suah(dict_dir, corpus_dir)
    return {
        lexeme: (
            "LEGIT" if counts[lexeme] >= _SUAH_LEGIT_FLOOR else "UNVERIFIED"
        )
        for lexeme in COMPOUND_SUAH_LEXEMES
    }


def scan_dict(
    dict_dir: Path,
    *,
    triage: bool = False,
    corpus_dir: Path | None = None,
) -> dict:
    """Run the authoritative-dictionary ZVS scan (report-only, exit always 0).

    Returns a summary dict with per-file, per-location, and per-form counts
    (plus the suah triage buckets when requested). No source file is written.
    """
    by_file: dict[str, Counter] = defaultdict(Counter)  # file -> form counts
    by_loc: dict[str, Counter] = defaultdict(Counter)  # location -> form counts
    by_form: Counter = Counter()
    total_invalid = 0
    total_violations = 0
    records_scanned = 0
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if not dict_dir.is_dir():
        print(
            f"[zvs-scan] WARNING: dict source not found at {dict_dir}. "
            "Skipping dict scan.",
            file=sys.stderr,
        )

    for source, text, location in _iter_dict_texts(dict_dir):
        records_scanned += 1
        if not _fast_has_forbidden(text):
            continue
        report = validate(text, source=source)
        if not report.is_valid:
            total_invalid += 1
        total_violations += len(report.violations)
        for v in report.violations:
            by_file[source][v.forbidden] += 1
            by_loc[location][v.forbidden] += 1
            by_form[v.forbidden] += 1

    suah_triage: dict[str, str] = {}
    if triage:
        suah_triage = triage_compound_suah(
            dict_dir, corpus_dir if corpus_dir is not None else DEFAULT_CORPUS
        )

    result = {
        "records_scanned": records_scanned,
        "invalid": total_invalid,
        "violations": total_violations,
        "by_file": {k: dict(v) for k, v in by_file.items()},
        "by_location": {k: dict(v) for k, v in by_loc.items()},
        "by_form": dict(by_form),
        "suah_triage": suah_triage,
    }

    json_dest = REPORT_DIR / f"dict-scan-{date.today().isoformat()}.json"
    json_dest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_dict_summary_md(result, REPORT_DIR / "dict-scan-summary.md")
    print(_fmt_dict_summary(result, verbose=False))
    print(f"[zvs-scan] wrote {json_dest}")
    return result


def _fmt_dict_summary(result: dict, *, verbose: bool = True) -> str:
    lines = [
        "# ZVS 2018 Dictionary (``--dict``) Scan Summary",
        "",
        f"- Generated: `{date.today().isoformat()}`",
        f"- Records yielded: **{result['records_scanned']}**",
        f"- Invalid snippets: **{result['invalid']}**",
        f"- Total violations: **{result['violations']}**",
        "",
        "> Report-only scan — non-blocking. Exit code is always 0. "
        "Dictionary data lives in the shared git-ignored `data/` container and "
        "is never edited or committed by this tool.",
        "",
        "## Violations by forbidden form",
        "",
        "| forbidden | count |",
        "|---|---|",
    ]
    for form, count in sorted(result["by_form"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{form}` | {count} |")
    lines += ["", "## Violations by location", "", "| location | count |", "|---|---|"]
    for loc, counter in sorted(result["by_location"].items()):
        total = sum(counter.values())
        detail = "".join(
            f" `{f}`:{c}" for f, c in sorted(counter.items())
        )
        lines.append(f"| {loc} | {total}{detail} |")
    lines += ["", "## Violations by file", "", "| file | count |", "|---|---|"]
    for src, counter in sorted(result["by_file"].items()):
        total = sum(counter.values())
        lines.append(f"| `{src}` | {total} |")
    if result["suah_triage"]:
        lines += ["", "## Compound-suah triage", "", "| lexeme | bucket |", "|---|---|"]
        buckets: Counter = Counter()
        for lexeme, bucket in result["suah_triage"].items():
            lines.append(f"| {lexeme} | {bucket} |")
            buckets[bucket] += 1
        lines += ["", "Buckets:"]
        for bucket, count in sorted(buckets.items()):
            lines.append(f"- {bucket}: {count}")
    if verbose:
        return "\n".join(lines)
    # Compact single-block line summary (printed to stdout by default).
    out: list[str] = []
    buckets: Counter = Counter(result["suah_triage"].values())
    out.append(
        f"[zvs-scan dict] scanned={result['records_scanned']} "
        f"invalid={result['invalid']} violations={result['violations']}"
    )
    if buckets:
        out.append(
            "[zvs-scan dict] suah-triage: "
            + ", ".join(f"{b}={n}" for b, n in sorted(buckets.items()))
        )
    return "\n".join(out)


def _write_dict_summary_md(result: dict, dest: Path) -> None:
    dest.write_text(_fmt_dict_summary(result, verbose=True), encoding="utf-8")


def _write_json(results: list[dict], dest: Path) -> None:
    payload = {
        "generated": date.today().isoformat(),
        "report_only": True,
        "total_sources": len(results),
        "total_invalid": sum(1 for r in results if not r["valid"]),
        "total_violations": sum(len(r["violations"]) for r in results),
        "results": results,
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_summary(
    results: list[dict],
    rules_by_id: dict[str, Counter],
    buckets: Counter,
    top_files: list[tuple[str, int]],
    dest: Path,
    *,
    gated: bool,
) -> None:
    total_invalid = sum(1 for r in results if not r["valid"])
    total_violations = sum(len(r["violations"]) for r in results)
    lines: list[str] = []
    lines.append("# ZVS 2018 Content Scan Summary")
    lines.append("")
    lines.append(f"- Generated: `{date.today().isoformat()}`")
    lines.append(f"- Sources scanned: **{len(results)}**")
    lines.append(f"- Sources with violations: **{total_invalid}**")
    lines.append(f"- Total violations: **{total_violations}**")
    lines.append("")
    if gated:
        lines.append(
            "> Regression-gated scan — exits 1 only on NEW or increased "
            "violations vs the committed baseline."
        )
    else:
        lines.append("> Report-only scan — non-blocking. Exit code is always 0.")
    lines.append("")

    lines.append("## Violations by rule")
    lines.append("")
    lines.append("| rule_id | forbidden | count |")
    lines.append("|---|---|---|")
    history: list[tuple[str, str, int]] = []
    for rule_id, counter in rules_by_id.items():
        for forbidden, count in counter.items():
            history.append((rule_id, forbidden, count))
    for rule_id, forbidden, count in sorted(history, key=lambda row: -row[2]):
        lines.append(f"| `{rule_id}` | `{forbidden}` | {count} |")
    lines.append("")

    lines.append("## Violations by forbidden token")
    lines.append("")
    forbidden_counter: Counter = Counter()
    for counter in rules_by_id.values():
        forbidden_counter.update(counter)
    lines.append("| forbidden token | count |")
    lines.append("|---|---|")
    for token, count in sorted(forbidden_counter.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{token}` | {count} |")
    lines.append("")

    lines.append("## Triage buckets")
    lines.append("")
    lines.append("| bucket | count |")
    lines.append("|---|---|")
    for bucket in _ALL_BUCKETS:
        lines.append(f"| {bucket} | {buckets[bucket]} |")
    lines.append("")

    lines.append("## Top files by violation count")
    lines.append("")
    lines.append("| source | violations |")
    lines.append("|---|---|")
    for source, count in top_files:
        lines.append(f"| `{source}` | {count} |")
    lines.append("")

    dest.write_text("\n".join(lines), encoding="utf-8")


def _write_baseline(composite_counts: Counter, dest: Path) -> None:
    """Write a committed baseline as a flat ``key -> count`` JSON dict."""
    payload: dict[str, int] = {
        str(key): int(count) for key, count in sorted(composite_counts.items())
    }
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _load_baseline(path: Path) -> dict[str, int]:
    """Load a baseline JSON dict, tolerating a missing/empty file.

    A missing baseline is reported as empty; the caller decides hard-fail.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"baseline {path} is not a JSON object")
    return {str(k): int(v) for k, v in raw.items()}


def _check_gate(composite_counts: Counter, baseline: dict[str, int]) -> list[tuple[str, int, int]]:
    """Return ``(key, baseline_count, current_count)`` for violations to flag.

    A combo is flagged when it is NEW (baseline_count == 0) or its current
    count exceeds the baseline count. Existing combos never flag.
    """
    flagged: list[tuple[str, int, int]] = []
    for key, current_count in sorted(composite_counts.items()):
        base_count = int(baseline.get(key, 0))
        if base_count == 0 or current_count > base_count:
            flagged.append((key, base_count, current_count))
    return flagged


def scan(
    wiki: bool,
    corpus: bool,
    *,
    gate: bool = False,
    baseline_file: Path | None = None,
    write_baseline_file: Path | None = None,
) -> int:
    """Run the scan and (optionally) the regression gate.

    Returns 0 on success (or no gate trip); 1 when the gate trips, the gate
    cannot be validated (missing wiki/baseline), or writing fails.
    """
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    rules_by_id: dict[str, Counter] = defaultdict(Counter)
    buckets: Counter = Counter()
    file_violations: Counter = Counter()
    composite_counts: Counter = Counter()

    scan_contexts = []

    if wiki:
        wiki_dir = DEFAULT_WIKI
        if not wiki_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: wiki source not found at {wiki_dir}. "
                "Skipping wiki scan (fresh checkout?).",
                file=sys.stderr,
            )
        else:
            scan_contexts.append(("wiki", wiki_dir, _iter_wiki_texts(wiki_dir)))

    if corpus:
        corpus_dir = DEFAULT_CORPUS
        if not corpus_dir.is_dir():
            print(
                f"[zvs-scan] WARNING: corpus not found at {corpus_dir}. "
                "Skipping corpus scan.",
                file=sys.stderr,
            )
        else:
            scan_contexts.append(("corpus", corpus_dir, _iter_corpus_texts(corpus_dir)))

    for _kind, _root, texts in scan_contexts:
        for source, text in texts:
            report = validate(text, source=source)
            _accumulate(
                results,
                report,
                rules_by_id,
                buckets,
                file_violations,
                composite_counts,
            )

    if write_baseline_file is not None:
        resolved = Path(write_baseline_file)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _write_baseline(composite_counts, resolved)
        print(f"[zvs-scan] wrote baseline {resolved}")

    json_dest = REPORT_DIR / f"zvs-scan-{date.today().isoformat()}.json"
    md_dest = REPORT_DIR / "zvs-scan-summary.md"
    top_files = file_violations.most_common(20)

    _write_json(results, json_dest)
    _write_summary(
        results,
        rules_by_id,
        buckets,
        top_files,
        md_dest,
        gated=gate,
    )

    scanned = len(results)
    invalid = sum(1 for r in results if not r["valid"])
    violations = sum(len(r["violations"]) for r in results)
    print(
        f"[zvs-scan] scanned={scanned} invalid={invalid} violations={violations}"
    )
    print(f"[zvs-scan] wrote {json_dest}")
    print(f"[zvs-scan] wrote {md_dest}")

    if not gate:
        return 0

    # ---- Regression gate ----
    if not scan_contexts:
        print(
            "[zvs-scan] GATE: no content sources present to scan "
            "(wiki/corpus missing). Gate cannot be validated -> FAIL.",
            file=sys.stderr,
        )
        return 1

    baseline_path = Path(baseline_file) if baseline_file else DEFAULT_BASELINE
    if not baseline_path.is_file():
        print(
            f"[zvs-scan] GATE: baseline not found at {baseline_path} -> FAIL.",
            file=sys.stderr,
        )
        return 1

    baseline = _load_baseline(baseline_path)

    flagged = _check_gate(composite_counts, baseline)
    if flagged:
        print(f"[zvs-scan] GATE: FAILED — {len(flagged)} new/increased violation(s).")
        print(f"{'key':90} baseline  current")
        for key, base_count, current_count in flagged:
            print(
                f"  {key}{' ' * max(1, 88 - len(key))} {base_count:8} {current_count:8}"
            )
        return 1

    print("[zvs-scan] GATE: PASS — no new or increased violations vs baseline.")
    return 0


def _accumulate(
    results: list[dict],
    report: Report,
    rules_by_id: dict[str, Counter],
    buckets: Counter,
    file_violations: Counter,
    composite_counts: Counter,
) -> None:
    results.append(report.to_dict())
    for v in report.violations:
        rules_by_id[v.rule_id][v.forbidden] += 1
        buckets[_triage_bucket(report, v)] += 1
        file_violations[report.source] += 1
        rel_source = _rel_source(report.source)
        composite_counts[_KEY_SEP.join((rel_source, v.rule_id, v.forbidden))] += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_content",
        description=(
            "ZVS 2018 content scanner. Report-only by default; "
            "--gate turns it into a regression check against a baseline."
        ),
    )
    parser.add_argument(
        "--wiki",
        dest="wiki",
        action="store_true",
        default=True,
        help="Scan wiki Markdown contents (default on).",
    )
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="Scan local corpus JSONL records (on-demand).",
    )
    parser.add_argument(
        "--dict",
        action="store_true",
        help=(
            "Scan the authoritative dictionary JSONL files (report-only, "
            "always exit 0). Uses ../data/dictionary/processed."
        ),
    )
    parser.add_argument(
        "--suah-triage",
        action="store_true",
        help=(
            "With --dict, also bucket compound-suah headwords into "
            "LEGIT / WRONG / UNVERIFIED using corpus-native attestation."
        ),
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help=(
            "Enforce regression gate: exit 1 iff a (source, rule, token) "
            "combo is NEW or its count exceeds the baseline."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Baseline JSON file (default: report/zvs-baseline.json). "
            "Used with --gate."
        ),
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="Write the current scan counts as a baseline JSON file.",
    )
    args = parser.parse_args(argv)
    rc = scan(
        wiki=args.wiki,
        corpus=args.corpus,
        gate=args.gate,
        baseline_file=args.baseline if args.gate else None,
        write_baseline_file=args.write_baseline,
    )
    if args.dict:
        scan_dict(
            DEFAULT_DICT,
            triage=args.suah_triage,
            corpus_dir=DEFAULT_CORPUS,
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())