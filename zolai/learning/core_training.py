"""Local core-engine retraining: stage, gate, evaluate, publish.

The two CRF models shipped in ``models/`` were trained by hand — no metric
persisted, no hash, no record of which data produced them. This module gives
the learning cycle a repeatable path:

1. train into ``models/runs/<run_id>/`` (never the canonical path),
2. evaluate on a genuinely held-out split (POS) or the gold test split
   (syllable) and write an ``eval_runs`` row,
3. refuse to publish when the metric is NaN or materially worse than the
   previous run for the same set,
4. publish with an atomic ``os.replace`` and keep ``previous-*`` for rollback.

POS gold is also its training source, so the full-set score is labeled
``in_sample`` in every result; only the split hold-out is a generalization
number.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.config import config
from zolai.learning.safety import sha256_file
from zolai.shared.clock import stamp

POS_SET = "pos_gold_v0"
SYLLABLE_SET = "syllable_gold_v0"
LEARNING_SET = "learning_run"

DEFAULT_POS_PATH = config.paths.root / "models" / "pos_tagger_crf.pkl"
DEFAULT_SYLLABLE_PATH = config.paths.root / "models" / "crf_syllable.pkl"
DEFAULT_SPLITS_DIR = config.paths.root / "data" / "syllable" / "splits"

MAX_METRIC_DROP_DEFAULT = 0.01


def _display_ref(path: Path | None) -> str | None:
    """Repo-relative ref for reports and the ledger — never a local absolute path."""
    if path is None:
        return None
    try:
        return str(Path(path).relative_to(config.paths.root))
    except ValueError:
        return Path(path).name


# ── eval-store plumbing (keyed to the passed engine, never the global one) ──


def ensure_eval_tables(engine: Engine) -> None:
    """Create ``eval_sets``/``eval_cases``/``eval_runs`` when absent."""
    from zolai.data.migrations import EVAL_CASES_DDL, EVAL_RUNS_DDL, EVAL_SETS_DDL

    with engine.begin() as conn:
        for ddl in (EVAL_SETS_DDL, EVAL_CASES_DDL, EVAL_RUNS_DDL):
            conn.execute(text(ddl))


def _ensure_eval_parent(engine: Engine, set_name: str, description: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO eval_sets (set_name, description, created_at, updated_at) "
                "VALUES (:n, :d, :t, :t) ON CONFLICT(set_name) DO NOTHING"
            ),
            {"n": set_name, "d": description, "t": stamp()},
        )


def _write_eval_run(
    engine: Engine,
    *,
    set_name: str,
    metrics: dict[str, float],
    case_count: int,
    duration_ms: float,
    gate_passed: bool,
) -> int | None:
    """Insert one ``eval_runs`` row on ``engine`` and return its id."""
    ensure_eval_tables(engine)
    _ensure_eval_parent(engine, set_name, f"local learning run — {set_name}")
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO eval_runs "
                "(set_name, created_at, case_count, duration_ms, gate_passed, "
                " metrics, source) "
                "VALUES (:set_name, :created_at, :case_count, :duration_ms, "
                ":gate_passed, :metrics, 'learning_run')"
            ),
            {
                "set_name": set_name,
                "created_at": stamp(),
                "case_count": int(case_count),
                "duration_ms": round(float(duration_ms), 3),
                "gate_passed": 1 if gate_passed else 0,
                "metrics": json.dumps(
                    {k: round(float(v), 6) for k, v in sorted(metrics.items())}
                ),
            },
        )
        return int(result.lastrowid) if result.lastrowid is not None else None


def previous_metric(engine: Engine, set_name: str, metric: str) -> float | None:
    """Latest recorded value of ``metric`` for ``set_name``, if any."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT metrics FROM eval_runs WHERE set_name = :n "
                    "ORDER BY id DESC LIMIT 10"
                ),
                {"n": set_name},
            ).fetchall()
    except Exception:  # noqa: BLE001 — a missing table means "no baseline"
        return None
    for (raw,) in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            continue
        value = payload.get(metric) if isinstance(payload, dict) else None
        if isinstance(value, (int, float)) and not math.isnan(float(value)):
            return float(value)
    return None


def check_gate(
    metric: str,
    value: float | None,
    baseline: float | None,
    *,
    max_drop: float = MAX_METRIC_DROP_DEFAULT,
    allow_drop: bool = False,
) -> dict[str, Any]:
    """Publish gate: no NaN, and no drop beyond ``max_drop`` vs the baseline."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return {
            "passed": False,
            "metric": metric,
            "value": None,
            "baseline": baseline,
            "reason": f"{metric} is missing or NaN",
        }
    if baseline is not None and value < baseline - max_drop and not allow_drop:
        return {
            "passed": False,
            "metric": metric,
            "value": value,
            "baseline": baseline,
            "reason": (
                f"{metric} {value:.4f} dropped more than {max_drop} "
                f"below the previous {baseline:.4f}"
            ),
        }
    return {
        "passed": True,
        "metric": metric,
        "value": value,
        "baseline": baseline,
        "reason": "",
    }


# ── artifact publishing ─────────────────────────────────────────────────────


def _publish(staging: Path, canonical: Path, run_dir: Path) -> str | None:
    """Copy ``staging`` over ``canonical`` atomically; keep ``previous-*``."""
    previous_sha = None
    if canonical.exists():
        previous_sha = sha256_file(canonical)
        shutil.copy2(canonical, run_dir / f"previous-{canonical.name}")
    canonical.parent.mkdir(parents=True, exist_ok=True)
    tmp = canonical.parent / f".{canonical.name}.tmp-{run_dir.name}"
    shutil.copy2(staging, tmp)
    os.replace(tmp, canonical)
    return previous_sha


def _write_manifest(run_dir: Path, payload: dict[str, Any]) -> Path:
    manifest = run_dir / "manifest.json"
    existing: dict[str, Any] = {}
    if manifest.exists():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing[payload["engine"]] = payload
    manifest.write_text(
        json.dumps(existing, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest


# ── POS ─────────────────────────────────────────────────────────────────────


def _load_pos_gold(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT payload FROM eval_cases "
                "WHERE set_name = :s AND is_active = 1 ORDER BY ordinal, id"
            ),
            {"s": POS_SET},
        ).fetchall()
    items: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            continue
        tokens = payload.get("tokens") or []
        texts = [str(t.get("text", "")) for t in tokens]
        tags = [str(t.get("pos", "X")) for t in tokens]
        if texts and len(texts) == len(tags):
            items.append({"text": payload.get("text", ""), "tokens": texts, "tags": tags})
    return items


def _pos_features(items: list[dict[str, Any]]):
    from zolai.pos_tagger.train import POSTaggerModel

    extractor = POSTaggerModel(None, [])  # type: ignore[arg-type]
    X = [
        [extractor._token_features(item["tokens"], i) for i in range(len(item["tokens"]))]
        for item in items
    ]
    y = [list(item["tags"]) for item in items]
    return X, y


def _pos_in_sample(model, items: list[dict[str, Any]]) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, f1_score

    gold: list[str] = []
    pred: list[str] = []
    for item in items:
        predicted = model.predict(item["text"])
        gold_tags = item["tags"]
        width = min(len(gold_tags), len(predicted))
        gold.extend(gold_tags[:width])
        pred.extend(list(predicted)[:width])
    if not gold:
        return {"available": False}
    return {
        "available": True,
        "in_sample": True,
        "macro_f1": float(f1_score(gold, pred, average="macro", zero_division=0)),
        "accuracy": float(accuracy_score(gold, pred)),
        "tokens": len(gold),
    }


def train_and_publish_pos(
    engine: Engine,
    *,
    run_id: str,
    staging_dir: str | Path,
    canonical_path: str | Path | None = None,
    test_size: float = 0.2,
    random_state: int = 42,
    max_metric_drop: float = MAX_METRIC_DROP_DEFAULT,
    allow_metric_drop: bool = False,
    stage_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Train the POS CRF on the engine's gold set, gate it, publish it."""
    from zolai.pos_tagger.train import train_pos_tagger

    canonical = Path(canonical_path or DEFAULT_POS_PATH)
    run_dir = Path(staging_dir)
    staging = run_dir / canonical.name

    items = _load_pos_gold(engine)
    if dry_run:
        return {
            "engine": "pos_crf",
            "status": "DRY_RUN",
            "metrics": {"gold_items": len(items)},
            "gate": {"passed": True, "reason": "dry-run: nothing trained"},
            "artifact_status": "STAGED",
        }
    if len(items) < 10:
        return {
            "engine": "pos_crf",
            "status": "SKIPPED",
            "metrics": {"gold_items": len(items)},
            "gate": {"passed": False, "reason": "not enough pos_gold_v0 items"},
            "artifact_status": "REJECTED",
        }

    run_dir.mkdir(parents=True, exist_ok=True)
    X, y = _pos_features(items)
    metrics: dict[str, Any] = {}
    started = datetime.now(timezone.utc)
    model = train_pos_tagger(
        model_path=staging,
        test_size=test_size,
        random_state=random_state,
        metrics_out=metrics,
        X=X,
        y=y,
    )
    # The trainer records the staging path for interactive CLI use; the cycle's
    # report and ledger carry refs only.
    metrics.pop("model_path", None)
    duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000

    metrics["in_sample"] = _pos_in_sample(model, items)
    value = metrics.get("macro_f1")
    gate = check_gate(
        "macro_f1",
        float(value) if value is not None else None,
        previous_metric(engine, POS_SET, "macro_f1"),
        max_drop=max_metric_drop,
        allow_drop=allow_metric_drop,
    )
    eval_run_id = _write_eval_run(
        engine,
        set_name=POS_SET,
        metrics={
            "macro_f1": float(metrics.get("macro_f1") or 0.0),
            "accuracy": float(metrics.get("accuracy") or 0.0),
            "in_sample_macro_f1": float(
                metrics["in_sample"].get("macro_f1", 0.0)
            ),
        },
        case_count=len(items),
        duration_ms=duration_ms,
        gate_passed=gate["passed"],
    )

    artifact_status = "REJECTED"
    previous_sha = None
    published_ref = None
    if gate["passed"]:
        if stage_only:
            artifact_status = "STAGED"
        else:
            previous_sha = _publish(staging, canonical, run_dir)
            published_ref = _display_ref(canonical)
            artifact_status = "PUBLISHED"
    elif staging.exists():
        staging.unlink()  # a rejected artifact must not look publishable

    result = {
        "engine": "pos_crf",
        "status": "OK" if gate["passed"] else "WARN",
        "artifact_status": artifact_status,
        "artifact_name": canonical.name,
        "staging_ref": _display_ref(staging) if staging.exists() else None,
        "published_ref": published_ref,
        "sha256": sha256_file(staging) if staging.exists() else None,
        "previous_sha256": previous_sha,
        "size_bytes": staging.stat().st_size if staging.exists() else None,
        "metrics": metrics,
        "gate": gate,
        "eval_run_id": eval_run_id,
        "model_reload_required": artifact_status == "PUBLISHED",
    }
    _write_manifest(run_dir, result)
    return result


# ── syllable ────────────────────────────────────────────────────────────────


def _syllable_counts(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def train_and_publish_syllable(
    engine: Engine,
    *,
    run_id: str,
    staging_dir: str | Path,
    canonical_path: str | Path | None = None,
    splits_dir: str | Path | None = None,
    max_metric_drop: float = MAX_METRIC_DROP_DEFAULT,
    allow_metric_drop: bool = False,
    stage_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Train the syllable CRF on the gold splits, gate on ``boundary_f1``."""
    canonical = Path(canonical_path or DEFAULT_SYLLABLE_PATH)
    splits = Path(splits_dir or DEFAULT_SPLITS_DIR)
    train_file = splits / "gold_train.jsonl"
    dev_file = splits / "gold_dev.jsonl"
    test_file = splits / "gold_test.jsonl"
    run_dir = Path(staging_dir)
    staging = run_dir / canonical.name

    if dry_run:
        return {
            "engine": "syllable_crf",
            "status": "DRY_RUN",
            "metrics": {
                "train_words": _syllable_counts(train_file),
                "dev_words": _syllable_counts(dev_file),
                "test_words": _syllable_counts(test_file),
            },
            "gate": {"passed": True, "reason": "dry-run: nothing trained"},
            "artifact_status": "STAGED",
        }

    missing = [p.name for p in (train_file, test_file) if not p.exists()]
    if missing:
        return {
            "engine": "syllable_crf",
            "status": "SKIPPED",
            "metrics": {"missing": missing},
            "gate": {"passed": False, "reason": f"missing gold splits: {', '.join(missing)}"},
            "artifact_status": "REJECTED",
        }

    from zolai.syllable.crf_segmenter import CRFSyllableSegmenter

    run_dir.mkdir(parents=True, exist_ok=True)
    segmenter = CRFSyllableSegmenter()
    started = datetime.now(timezone.utc)
    train_stats = segmenter.train(
        train_file, dev_file=dev_file if dev_file.exists() else None
    )
    metrics = segmenter.evaluate(test_file)
    segmenter.save(staging)
    duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    metrics.update(
        {
            "split": "gold_test",
            "train_words": train_stats.get("train_size", 0),
            "total_chars": train_stats.get("total_chars", 0),
            "dev": train_stats.get("dev_stats", {}),
        }
    )

    value = metrics.get("boundary_f1")
    gate = check_gate(
        "boundary_f1",
        float(value) if value is not None else None,
        previous_metric(engine, SYLLABLE_SET, "boundary_f1"),
        max_drop=max_metric_drop,
        allow_drop=allow_metric_drop,
    )
    eval_run_id = _write_eval_run(
        engine,
        set_name=SYLLABLE_SET,
        metrics={
            "boundary_f1": float(metrics.get("boundary_f1") or 0.0),
            "word_accuracy": float(metrics.get("word_accuracy") or 0.0),
            "syllable_count_accuracy": float(
                metrics.get("syllable_count_accuracy") or 0.0
            ),
        },
        case_count=int(metrics.get("total_words") or 0),
        duration_ms=duration_ms,
        gate_passed=gate["passed"],
    )

    artifact_status = "REJECTED"
    previous_sha = None
    published_ref = None
    if gate["passed"]:
        if stage_only:
            artifact_status = "STAGED"
        else:
            previous_sha = _publish(staging, canonical, run_dir)
            published_ref = _display_ref(canonical)
            artifact_status = "PUBLISHED"
    elif staging.exists():
        staging.unlink()

    result = {
        "engine": "syllable_crf",
        "status": "OK" if gate["passed"] else "WARN",
        "artifact_status": artifact_status,
        "artifact_name": canonical.name,
        "staging_ref": _display_ref(staging) if staging.exists() else None,
        "published_ref": published_ref,
        "sha256": sha256_file(staging) if staging.exists() else None,
        "previous_sha256": previous_sha,
        "size_bytes": staging.stat().st_size if staging.exists() else None,
        "metrics": metrics,
        "gate": gate,
        "eval_run_id": eval_run_id,
        "model_reload_required": artifact_status == "PUBLISHED",
    }
    _write_manifest(run_dir, result)
    return result


def record_aggregate_eval(
    engine: Engine,
    *,
    pos: dict[str, Any],
    syllable: dict[str, Any],
    duration_ms: float,
    gate_passed: bool,
) -> int | None:
    """One ``learning_run`` eval row summarising both engines."""
    metrics = {
        "pos_macro_f1": float((pos.get("metrics") or {}).get("macro_f1") or 0.0),
        "syllable_boundary_f1": float(
            (syllable.get("metrics") or {}).get("boundary_f1") or 0.0
        ),
    }
    case_count = int((syllable.get("metrics") or {}).get("total_words") or 0)
    return _write_eval_run(
        engine,
        set_name=LEARNING_SET,
        metrics=metrics,
        case_count=case_count,
        duration_ms=duration_ms,
        gate_passed=gate_passed,
    )
