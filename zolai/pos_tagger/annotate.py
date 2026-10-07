"""POS Annotation Tool — CLI for building Zolai POS gold set."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from zolai.config import config as settings

app = typer.Typer(help="POS Annotation Tool for Zolai")
console = Console()

# Gold set file
GOLD_FILE = Path("data/eval/pos_gold_v0.jsonl")

# Zomi POS tagset (simplified UD + Zomi-specific)
POS_TAGS = {
    "NOUN": "Noun",
    "VERB": "Verb",
    "ADJ": "Adjective",
    "ADV": "Adverb",
    "PRON": "Pronoun",
    "DET": "Determiner",
    "ADP": "Adposition",
    "CONJ": "Conjunction",
    "PART": "Particle",
    "NUM": "Numeral",
    "PUNCT": "Punctuation",
    "SYM": "Symbol",
    "X": "Other",
    # Zomi-specific
    "DIR": "Directional",
    "ASP": "Aspect",
    "CLF": "Classifier",
}

# Morphological features
MORPH_FEATURES = {
    "Tense": ["Past", "Pres", "Fut"],
    "Aspect": ["Perf", "Imp", "Prog"],
    "Mood": ["Ind", "Sub", "Imp"],
    "Number": ["Sing", "Plur"],
    "Person": ["1", "2", "3"],
    "Case": ["Nom", "Acc", "Erg", "Gen", "Dat"],
}


def get_db_connection():
    """Get SQLite connection to the live database."""
    db_path = "/home/peter/Documents/Projects/zolai-ai/data/zolai.db"
    return sqlite3.connect(db_path)


def load_sentences(limit: int = 1000) -> list[dict[str, Any]]:
    """Load sentences from bible_verses and dictionary examples."""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sentences = []

    # From bible_verses (Zolai text)
    cursor.execute("""
        SELECT zo_tdb77 as text, 'bible' as source, rowid as sentence_id
        FROM bible_verses
        WHERE zo_tdb77 IS NOT NULL AND zo_tdb77 != ''
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit // 2,))
    for row in cursor.fetchall():
        sentences.append(dict(row))

    # From dictionary examples (if available)
    cursor.execute("""
        SELECT zolai as text, 'dictionary' as source, rowid as sentence_id
        FROM dictionary
        WHERE zolai IS NOT NULL AND zolai != ''
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit // 2,))
    for row in cursor.fetchall():
        sentences.append(dict(row))

    conn.close()
    return sentences


def tokenize_zolai(text: str) -> list[str]:
    """Simple Zolai tokenizer (space-based for now)."""
    # TODO: Use proper syllable-based tokenizer
    return text.split()


def load_existing_annotations() -> dict[str, dict]:
    """Load existing annotations from gold file."""
    if not GOLD_FILE.exists():
        return {}
    annotations = {}
    with open(GOLD_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            item = json.loads(line.strip())
            annotations[item['sentence_id']] = item
    return annotations


def save_annotation(annotation: dict[str, Any]) -> None:
    """Save annotation to gold file (append or update)."""
    annotations = load_existing_annotations()
    annotations[annotation['sentence_id']] = annotation

    GOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GOLD_FILE, 'w', encoding='utf-8') as f:
        for ann in annotations.values():
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')


@app.command()
def list_sentences(
    limit: int = typer.Option(20, help="Number of sentences to show"),
    unannotated_only: bool = typer.Option(True, help="Show only unannotated sentences"),
):
    """List sentences available for annotation."""
    sentences = load_sentences(limit * 2)
    existing = load_existing_annotations()

    table = Table(title="Sentences for POS Annotation")
    table.add_column("ID", style="cyan")
    table.add_column("Source", style="green")
    table.add_column("Text", style="white")
    table.add_column("Status", style="yellow")

    count = 0
    for sent in sentences:
        sid = str(sent['sentence_id'])
        status = "✓ Annotated" if sid in existing else "○ Pending"
        if unannotated_only and sid in existing:
            continue
        table.add_row(sid, sent['source'], sent['text'][:80] + ("..." if len(sent['text']) > 80 else ""), status)
        count += 1
        if count >= limit:
            break

    console.print(table)
    console.print(f"Total: {count} shown, {len(existing)} already annotated")


@app.command()
def next_sentence():
    """Get the next unannotated sentence for annotation."""
    sentences = load_sentences(100)
    existing = load_existing_annotations()

    for sent in sentences:
        sid = str(sent['sentence_id'])
        if sid not in existing:
            tokens = tokenize_zolai(sent['text'])
            console.print(f"[bold cyan]Sentence ID:[/bold cyan] {sid}")
            console.print(f"[bold green]Source:[/bold green] {sent['source']}")
            console.print(f"[bold white]Text:[/bold white] {sent['text']}")
            console.print(f"[bold yellow]Tokens:[/bold yellow] {tokens}")

            # Show suggested annotation template
            template = {
                "sentence_id": sid,
                "text": sent['text'],
                "source": sent['source'],
                "tokens": [{"text": t, "lemma": "", "pos": "", "features": {}} for t in tokens]
            }
            console.print("\n[dim]Copy this template to annotate:[/dim]")
            console.print(json.dumps(template, ensure_ascii=False, indent=2))
            return

    console.print("[green]All sentences annotated![/green]")


@app.command()
def annotate(
    sentence_id: str = typer.Argument(..., help="Sentence ID to annotate"),
    interactive: bool = typer.Option(True, help="Interactive annotation mode"),
):
    """Annotate a specific sentence."""
    sentences = load_sentences(1000)
    sent = next((s for s in sentences if str(s['sentence_id']) == sentence_id), None)

    if not sent:
        console.print(f"[red]Sentence {sentence_id} not found[/red]")
        return

    tokens = tokenize_zolai(sent['text'])
    existing = load_existing_annotations()

    if sentence_id in existing and not Confirm.ask("Sentence already annotated. Overwrite?"):
        return

    if interactive:
        console.print(f"[bold]Annotating:[/bold] {sent['text']}")
        console.print(f"[bold]Tokens:[/bold] {tokens}")
        console.print(f"\n[dim]Available POS tags: {', '.join(POS_TAGS.keys())}[/dim]")

        annotated_tokens = []
        for i, token in enumerate(tokens):
            console.print(f"\n[cyan]Token {i+1}/{len(tokens)}:[/cyan] [white]{token}[/white]")

            pos = Prompt.ask("POS", choices=list(POS_TAGS.keys()), default="X")
            lemma = Prompt.ask("Lemma", default=token.lower())

            features = {}
            if Confirm.ask("Add morphological features?", default=False):
                for feat, values in MORPH_FEATURES.items():
                    val = Prompt.ask(f"  {feat}", choices=values + [""], default="")
                    if val:
                        features[feat] = val

            annotated_tokens.append({
                "text": token,
                "lemma": lemma,
                "pos": pos,
                "features": features
            })

        annotation = {
            "sentence_id": sentence_id,
            "text": sent['text'],
            "source": sent['source'],
            "tokens": annotated_tokens
        }
    else:
        # Non-interactive: expect JSON from stdin
        import sys
        annotation = json.load(sys.stdin)

    save_annotation(annotation)
    console.print(f"[green]Saved annotation for sentence {sentence_id}[/green]")


@app.command()
def review(limit: int = typer.Option(10, help="Number of annotations to review")):
    """Review existing annotations."""
    existing = load_existing_annotations()

    if not existing:
        console.print("[yellow]No annotations yet[/yellow]")
        return

    for sid, ann in list(existing.items())[:limit]:
        console.print(f"\n[bold cyan]Sentence {sid}:[/bold cyan] {ann['text']}")
        table = Table()
        table.add_column("Token")
        table.add_column("Lemma")
        table.add_column("POS")
        table.add_column("Features")

        for tok in ann['tokens']:
            table.add_row(
                tok['text'],
                tok['lemma'] or "-",
                tok['pos'] or "-",
                json.dumps(tok['features']) if tok['features'] else "-"
            )
        console.print(table)


@app.command()
def export(
    output: Path = typer.Option(GOLD_FILE, help="Output file"),
    format: str = typer.Option("jsonl", help="Export format: jsonl or json"),
):
    """Export annotations to file."""
    existing = load_existing_annotations()

    if format == "jsonl":
        with open(output, 'w', encoding='utf-8') as f:
            for ann in existing.values():
                f.write(json.dumps(ann, ensure_ascii=False) + '\n')
    else:
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(list(existing.values()), f, ensure_ascii=False, indent=2)

    console.print(f"[green]Exported {len(existing)} annotations to {output}[/green]")


@app.command()
def stats():
    """Show annotation statistics."""
    existing = load_existing_annotations()

    if not existing:
        console.print("[yellow]No annotations yet[/yellow]")
        return

    total_sentences = len(existing)
    total_tokens = sum(len(ann['tokens']) for ann in existing.values())
    pos_counts = {}

    for ann in existing.values():
        for tok in ann['tokens']:
            pos = tok['pos'] or "UNK"
            pos_counts[pos] = pos_counts.get(pos, 0) + 1

    console.print(f"[bold]Total Sentences:[/bold] {total_sentences}")
    console.print(f"[bold]Total Tokens:[/bold] {total_tokens}")
    console.print(f"[bold]Avg Tokens/Sentence:[/bold] {total_tokens / total_sentences:.1f}")

    table = Table(title="POS Distribution")
    table.add_column("POS")
    table.add_column("Count")
    table.add_column("%")

    for pos, count in sorted(pos_counts.items(), key=lambda x: -x[1]):
        table.add_row(pos, str(count), f"{count/total_tokens*100:.1f}%")

    console.print(table)


if __name__ == "__main__":
    app()
