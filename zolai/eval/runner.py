"""ZolaiBench v0.1 Evaluation Runner — Fully Database-Driven."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from zolai.config import config
from zolai.eval.metrics import (
    compute_grammar_metrics,
    compute_morphology_metrics,
    compute_pos_metrics,
    compute_tokenization_metrics,
)
from zolai.eval.store import (
    GOLD_SETS,
    GOLD_TASKS,
    export_eval_set_to_jsonl,
    get_eval_items,
    init_eval_db,
    load_gold_from_jsonl,
)

app = typer.Typer(help="ZolaiBench v0.1 — Evaluation Runner")
console = Console()


@dataclass
class EvaluationResult:
    """Result of an evaluation run."""
    task: str
    metrics: dict[str, Any]
    duration_ms: float
    item_count: int
    #: What produced the predictions. Carried into the report so a score is
    #: never detached from the system that earned it.
    system: str = "placeholder"


#: The predictors below are deterministic stubs — Zolai has no trained segmenter
#: or grammar-error model, and the one trained POS tagger is scored on the same
#: gold it was trained on. A number from ``zolai eval run`` is therefore a check
#: that the *harness* scores gold data correctly, not a model score, and it is
#: labelled as a placeholder everywhere it appears. A real system replaces its
#: entry here (or ``--system`` names one) and the label travels with it.
PREDICTORS: dict[str, str] = {
    "tokenization": "placeholder:whitespace-split",
    "pos": "placeholder:all-X",
    "morph": "placeholder:whitespace-split",
    "grammar": "placeholder:no-errors",
}

#: POS systems that are real artifacts rather than stubs. ``in-sample`` is in the
#: label on purpose: ``zolai/pos_tagger/train.py`` trains on ``pos_gold_v0`` and
#: ``evaluate_on_gold`` re-reads that same set, so nothing has ever held data out.
#: Reporting the number without that word would claim generalization we have not
#: measured — 99.6% on the set the model memorized is a memorization score.
POS_SYSTEMS: dict[str, str] = {
    "placeholder": "placeholder:all-X",
    "crf": "crf:pos_tagger_crf.pkl(in-sample)",
}


def load_pos_predictor(system: str):
    """Return ``(label, predict_fn)`` for a named POS system.

    Raises :class:`SystemExit` with the reason rather than falling back to the
    placeholder: a silent downgrade would print a placeholder score under a real
    system's name, which is the exact confusion this label exists to prevent.
    """
    if system not in POS_SYSTEMS:
        console.print(f"[red]Unknown --system {system!r}[/red]  (choose: {', '.join(POS_SYSTEMS)})")
        raise typer.Exit(1)
    if system == "placeholder":
        return POS_SYSTEMS[system], predict_pos

    from zolai.config import config

    model_path = config.paths.root / "models" / "pos_tagger_crf.pkl"
    if not model_path.exists():
        console.print(
            f"[red]CRF model not found: {model_path}[/red]  "
            "(train it with `python -m zolai.pos_tagger.train`)"
        )
        raise typer.Exit(1)
    import pickle  # noqa: S403 — first-party artifact from zolai/pos_tagger/train.py

    try:
        from zolai.pos_tagger.train import POSTaggerModel

        # train.py is run as a script, so the artifact it saved names its class
        # `__main__.POSTaggerModel` — a path that resolves to nothing once the
        # library, rather than the script, is doing the loading. That is why this
        # model has never reached the harness: it pickled under a module no reader
        # can import. Redirect that one name instead of requiring a retrain.
        class _Redirect(pickle.Unpickler):
            def find_class(self, module: str, name: str):
                if module == "__main__" and name == "POSTaggerModel":
                    return POSTaggerModel
                return super().find_class(module, name)

        with model_path.open("rb") as fh:
            model = _Redirect(fh).load()
    except (AttributeError, ImportError, OSError, pickle.UnpicklingError) as exc:
        console.print(f"[red]CRF model unreadable: {exc}[/red]")
        raise typer.Exit(1)
    return POS_SYSTEMS[system], model.predict


# Placeholder predictors (to be replaced with actual models)
def predict_tokenization(text: str) -> list[str]:
    """Predict tokenization (placeholder: space-based)."""
    return text.split()


def predict_pos(text: str) -> list[str]:
    """Predict POS tags (placeholder: all X)."""
    return ["X"] * len(text.split())


def predict_morphology(text: str) -> list[str]:
    """Predict morphological segmentation (placeholder: space-based)."""
    return text.split()


def predict_grammar_errors(text: str) -> list[dict]:
    """Predict grammar errors (placeholder: none)."""
    return []


def run_tokenization_eval(gold_items: list, smoke: bool = False) -> EvaluationResult:
    """Run tokenization evaluation."""
    start = time.time()
    limit = 50 if smoke else None
    items = gold_items[:limit] if limit else gold_items

    predicted = []
    gold = []

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_tokens = [t['text'] for t in ann.get('tokens', [])]
        pred_tokens = predict_tokenization(text)

        predicted.append(pred_tokens)
        gold.append(gold_tokens)

    metrics = compute_tokenization_metrics(predicted, gold)
    return EvaluationResult(
        task="tokenization",
        metrics=metrics.to_dict(),
        duration_ms=(time.time() - start) * 1000,
        item_count=len(items),
        system=PREDICTORS["tokenization"],
    )


def run_pos_eval(
    gold_items: list, smoke: bool = False, system: str = "placeholder"
) -> EvaluationResult:
    """Run POS tagging evaluation with a named system."""
    start = time.time()
    label, predict = load_pos_predictor(system)
    limit = 50 if smoke else None
    items = gold_items[:limit] if limit else gold_items

    predicted = []
    gold = []
    tagged_tokens = 0
    scored_tokens = 0

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_tags = [t.get('pos', 'X') for t in ann.get('tokens', [])]
        pred_tags = predict(text)

        # Align lengths
        min_len = min(len(gold_tags), len(pred_tags))
        predicted.append(pred_tags[:min_len])
        gold.append(gold_tags[:min_len])
        tagged_tokens += sum(1 for t in gold_tags[:min_len] if t != "X")
        scored_tokens += min_len

    metrics = compute_pos_metrics(predicted, gold).to_dict()
    # Half the lane is `X` — untagged tokens the annotation pass never resolved.
    # Macro F1 averages over present classes, so without this number a score is
    # read against 14,125 tokens when it was earned on the ~6,800 that are tagged.
    metrics["tagged_coverage"] = (tagged_tokens / scored_tokens) if scored_tokens else 0.0
    return EvaluationResult(
        task="pos",
        metrics=metrics,
        duration_ms=(time.time() - start) * 1000,
        item_count=len(items),
        system=label,
    )


def run_morphology_eval(gold_items: list, smoke: bool = False) -> EvaluationResult:
    """Run morphology evaluation."""
    start = time.time()
    limit = 50 if smoke else None
    items = gold_items[:limit] if limit else gold_items

    predicted = []
    gold = []

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_segs = [t['text'] for t in ann.get('tokens', [])]
        pred_segs = predict_morphology(text)

        predicted.append(pred_segs)
        gold.append(gold_segs)

    metrics = compute_morphology_metrics(predicted, gold)
    return EvaluationResult(
        task="morphology",
        metrics=metrics.to_dict(),
        duration_ms=(time.time() - start) * 1000,
        item_count=len(items),
        system=PREDICTORS["morph"],
    )


def run_grammar_eval(gold_items: list, smoke: bool = False) -> EvaluationResult:
    """Run grammar error detection evaluation."""
    start = time.time()
    limit = 50 if smoke else None
    items = gold_items[:limit] if limit else gold_items

    predicted_errors = []
    gold_errors = []

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_errs = ann.get('errors', [])
        pred_errs = predict_grammar_errors(text)

        predicted_errors.append(pred_errs)
        gold_errors.append(gold_errs)

    metrics = compute_grammar_metrics(predicted_errors, gold_errors)
    return EvaluationResult(
        task="grammar",
        metrics=metrics.to_dict(),
        duration_ms=(time.time() - start) * 1000,
        item_count=len(items),
        system=PREDICTORS["grammar"],
    )


@app.command()
def run(
    task: str = typer.Argument(..., help="Task: tokenization, pos, morph, grammar, all"),
    smoke: bool = typer.Option(False, help="Run smoke test (50 items per task)"),
    system: str = typer.Option(
        "placeholder",
        "--system",
        help="POS system to score: 'placeholder' (floor) or 'crf' (trained model, in-sample)",
    ),
):
    """Run evaluation for specified task(s) — reads from evaluation database."""
    # Ensure eval DB is initialized
    init_eval_db()

    # Validated here rather than inside the predictor: an unknown --system used to
    # pass quietly whenever the lane had no gold rows, because the loop `continue`d
    # before the scorer was ever reached.
    if task in ("pos", "all") and system not in POS_SYSTEMS:
        console.print(f"[red]Unknown --system {system!r}[/red]  (choose: {', '.join(POS_SYSTEMS)})")
        raise typer.Exit(1)

    tasks = ["tokenization", "pos", "morph", "grammar"] if task == "all" else [task]

    results = []

    for t in tasks:
        console.print(f"\n[bold cyan]Running {t} evaluation...[/bold cyan]")

        # Get gold items from evaluation database
        eval_set_name = GOLD_SETS[t][0] if t in GOLD_TASKS else t
        gold_items = get_eval_items(eval_set_name)

        if not gold_items:
            console.print(f"[yellow]No gold data in eval DB for {t} (eval set: {eval_set_name})[/yellow]")
            console.print(f"  Run 'zolai eval init-gold {t}' to load from JSONL, or add items directly to DB")
            continue

        console.print(f"  Items from DB: {len(gold_items)}")

        if t == "tokenization":
            result = run_tokenization_eval(gold_items, smoke)
        elif t == "pos":
            result = run_pos_eval(gold_items, smoke, system=system)
        elif t == "morph":
            result = run_morphology_eval(gold_items, smoke)
        elif t == "grammar":
            result = run_grammar_eval(gold_items, smoke)
        else:
            console.print(f"[red]Unknown task: {t}[/red]")
            continue

        results.append(result)

        # Print metrics
        table = Table(title=f"{t.capitalize()} Metrics")
        table.add_column("Metric")
        table.add_column("Value")
        for k, v in result.metrics.items():
            if isinstance(v, dict):
                table.add_row(k, json.dumps(v, indent=2))
            else:
                table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
        console.print(table)
        console.print(f"  Duration: {result.duration_ms:.1f}ms")

    # Summary
    if results:
        console.print("\n[bold green]Summary[/bold green]")
        summary_table = Table()
        summary_table.add_column("Task")
        summary_table.add_column("Items")
        summary_table.add_column("Key Metric")
        summary_table.add_column("System")

        for r in results:
            # ``run_morphology_eval`` reports the task as "morphology"; matching
            # only the CLI alias used to drop its metric to "N/A" in the summary.
            if r.task == "tokenization":
                key = f"F1: {r.metrics.get('f1', 0):.4f}"
            elif r.task == "pos":
                key = (
                    f"Macro F1: {r.metrics.get('macro_f1', 0):.4f} "
                    f"(tagged coverage {r.metrics.get('tagged_coverage', 0):.1%})"
                )
            elif r.task in ("morph", "morphology"):
                key = f"Exact Match: {r.metrics.get('exact_match', 0):.4f}"
            elif r.task == "grammar":
                key = f"Error F1: {r.metrics.get('error_f1', 0):.4f}"
            else:
                key = "N/A"
            summary_table.add_row(r.task, str(r.item_count), key, r.system)

        console.print(summary_table)
        if any(r.system.startswith("placeholder:") for r in results):
            console.print(
                "[yellow]These predictors are placeholders — no trained Zolai model exists for them.[/yellow] "
                "The scores validate the harness and the gold data, and are the floor a real "
                "system must beat. They are not model quality."
            )
        if any("(in-sample)" in r.system for r in results):
            console.print(
                "[yellow]That system was trained on the set it was just scored on.[/yellow] "
                "`zolai/pos_tagger/train.py` fits `pos_gold_v0` and reports back against the same "
                "rows, so this measures memorization, not generalization. Until a held-out set "
                "exists, no POS number from this command is a model-quality claim."
            )


@app.command("init-gold")
def init_gold(
    task: str = typer.Argument(..., help="Task to initialize from JSONL"),
):
    """Initialize gold set in evaluation DB from JSONL file (one-time migration)."""
    init_eval_db()

    if task not in GOLD_SETS:
        console.print(f"[red]Unknown task: {task}[/red]")
        raise typer.Exit(1)

    gold_file = config.paths.data / "eval" / f"{task}_gold_v0.jsonl"
    if not gold_file.exists():
        console.print(f"[red]Gold file not found: {gold_file}[/red]")
        raise typer.Exit(1)

    eval_set, description = GOLD_SETS[task]
    count = load_gold_from_jsonl(gold_file, eval_set, task, description)
    console.print(f"[green]Loaded {count} items into eval set '{eval_set}'[/green]")


@app.command()
def export(
    task: str = typer.Argument(..., help="Task to export"),
    output: Path = typer.Option(None, help="Output file"),
):
    """Export eval set from DB to JSONL (for backup/portability)."""
    init_eval_db()

    if task not in GOLD_SETS:
        console.print(f"[red]Unknown task: {task}[/red]")
        raise typer.Exit(1)

    eval_set, _ = GOLD_SETS[task]
    out_file = output or (config.paths.data / "eval" / f"{task}_gold_v0.jsonl")
    count = export_eval_set_to_jsonl(eval_set, out_file)
    console.print(f"[green]Exported {count} items to {out_file}[/green]")


@app.command()
def list_tasks():
    """List available evaluation tasks."""
    init_eval_db()

    table = Table(title="ZolaiBench v0.1 Tasks")
    table.add_column("Task")
    table.add_column("Description")
    table.add_column("Eval Set (DB)")
    table.add_column("Items in DB")

    for task in GOLD_TASKS:
        eval_set, desc = GOLD_SETS[task]
        items = get_eval_items(eval_set)
        table.add_row(task, desc, eval_set, str(len(items)))

    console.print(table)


@app.command()
def stats():
    """Show evaluation database statistics."""
    init_eval_db()

    from zolai.eval.store import list_eval_sets
    sets = list_eval_sets()

    table = Table(title="Evaluation Database Stats")
    table.add_column("Eval Set")
    table.add_column("Task")
    table.add_column("Version")
    table.add_column("Items")
    table.add_column("Created")

    for s in sets:
        table.add_row(s.name, s.task, s.version, str(s.item_count), s.created_at)

    console.print(table)


if __name__ == "__main__":
    app()
