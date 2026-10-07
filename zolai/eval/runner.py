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

from zolai.eval.metrics import (
    compute_grammar_metrics,
    compute_morphology_metrics,
    compute_pos_metrics,
    compute_tokenization_metrics,
)
from zolai.eval.store import (
    GOLD_SETS,
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
    )


def run_pos_eval(gold_items: list, smoke: bool = False) -> EvaluationResult:
    """Run POS tagging evaluation."""
    start = time.time()
    limit = 50 if smoke else None
    items = gold_items[:limit] if limit else gold_items

    predicted = []
    gold = []

    for item in items:
        ann = json.loads(item.gold_annotation)
        text = ann.get('text', '')
        gold_tags = [t.get('pos', 'X') for t in ann.get('tokens', [])]
        pred_tags = predict_pos(text)

        # Align lengths
        min_len = min(len(gold_tags), len(pred_tags))
        predicted.append(pred_tags[:min_len])
        gold.append(gold_tags[:min_len])

    metrics = compute_pos_metrics(predicted, gold)
    return EvaluationResult(
        task="pos",
        metrics=metrics.to_dict(),
        duration_ms=(time.time() - start) * 1000,
        item_count=len(items),
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
    )


@app.command()
def run(
    task: str = typer.Argument(..., help="Task: tokenization, pos, morph, grammar, all"),
    smoke: bool = typer.Option(False, help="Run smoke test (50 items per task)"),
):
    """Run evaluation for specified task(s) — reads from evaluation database."""
    # Ensure eval DB is initialized
    init_eval_db()

    tasks = ["tokenization", "pos", "morph", "grammar"] if task == "all" else [task]

    results = []

    for t in tasks:
        console.print(f"\n[bold cyan]Running {t} evaluation...[/bold cyan]")

        # Get gold items from evaluation database
        eval_set_name = GOLD_SETS.get(t, (t, ""))[0]
        gold_items = get_eval_items(eval_set_name)

        if not gold_items:
            console.print(f"[yellow]No gold data in eval DB for {t} (eval set: {eval_set_name})[/yellow]")
            console.print(f"  Run 'zolai eval init-gold {t}' to load from JSONL, or add items directly to DB")
            continue

        console.print(f"  Items from DB: {len(gold_items)}")

        if t == "tokenization":
            result = run_tokenization_eval(gold_items, smoke)
        elif t == "pos":
            result = run_pos_eval(gold_items, smoke)
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

        for r in results:
            if r.task == "tokenization":
                key = f"F1: {r.metrics.get('f1', 0):.4f}"
            elif r.task == "pos":
                key = f"Macro F1: {r.metrics.get('macro_f1', 0):.4f}"
            elif r.task == "morph":
                key = f"Exact Match: {r.metrics.get('exact_match', 0):.4f}"
            elif r.task == "grammar":
                key = f"Error F1: {r.metrics.get('error_f1', 0):.4f}"
            else:
                key = "N/A"
            summary_table.add_row(r.task, str(r.item_count), key)

        console.print(summary_table)


@app.command("init-gold")
def init_gold(
    task: str = typer.Argument(..., help="Task to initialize from JSONL"),
):
    """Initialize gold set in evaluation DB from JSONL file (one-time migration)."""
    init_eval_db()

    if task not in GOLD_SETS:
        console.print(f"[red]Unknown task: {task}[/red]")
        raise typer.Exit(1)

    gold_file = Path("data/eval") / f"{task}_gold_v0.jsonl"
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
    out_file = output or (Path("data/eval") / f"{task}_gold_v0.jsonl")
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

    for task, (eval_set, desc) in GOLD_SETS.items():
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
