"""Zolai Toolkit — Unified CLI (Typer)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config import config
from ..dictionary.manager import DictionaryManager
from .discovery import discovery_app
from .incremental import app as incremental_app
from .knowledge import app as knowledge_app
from .observation import observation_app
from .profile import app as profile_app
from .publish import app as publish_app
from .rag import app as rag_app
from .release import app as release_app

app = typer.Typer(
    name="zolai",
    help="🦜 Zolai Toolkit — Unified language data pipeline for Zolai (Tedim/Zomi)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()

# Phase 2 §36 — observation engine (build | refresh-index | bloom)
app.add_typer(observation_app, name="observation")

# Phase 3 §36 — discovery engine (build)
app.add_typer(discovery_app, name="discovery")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(incremental_app, name="incremental")
app.add_typer(rag_app, name="rag")
app.add_typer(publish_app, name="publish")
app.add_typer(release_app, name="release")
app.add_typer(profile_app, name="profile")


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ============================================================
# CRAWLER
# ============================================================


@app.command()
def crawl(
    seed: str = typer.Argument(..., help="Seed URL or search topic"),
    depth: int = typer.Option(2, "--depth", "-d", help="Max crawl depth"),
    max_pages: int = typer.Option(50, "--max-pages", "-m", help="Max pages to crawl"),
    infinite: bool = typer.Option(False, "--infinite", "-i", help="Run infinite crawl loop"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔍 Crawl web sources for Zolai content."""
    _setup_logging(verbose)
    from ..crawler.engine import CrawlEngine

    config.paths.ensure_dirs()
    engine = CrawlEngine()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"Crawling '{seed}'...", total=None)
        if infinite:
            rprint("[yellow]Starting infinite crawl loop (Ctrl+C to stop)...[/yellow]")
            engine.infinite_loop()
        else:
            results = engine.crawl_seed(seed)
            progress.update(task, completed=True)
            rprint(f"[green]✓ Crawled {len(results)} pages from '{seed}'[/green]")


# ============================================================
# CLEANER
# ============================================================


@app.command()
def clean(
    input_dir: str = typer.Option(None, "--input", "-i", help="Input directory (default: data/raw)"),
    output_dir: str = typer.Option(None, "--output", "-o", help="Output directory (default: data/cleaned)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🧹 Clean and normalize raw data."""
    _setup_logging(verbose)
    from ..cleaner.pipeline import CleanPipeline

    config.paths.ensure_dirs()
    pipeline = CleanPipeline()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Cleaning data...", total=None)
        result = pipeline.run_full_pipeline()
        progress.update(task, completed=True)
        rprint(f"[green]✓ Cleaned: {result}[/green]")


@app.command()
def dedup(
    input_dir: str = typer.Option(None, "--input", "-i", help="Input directory"),
    threshold: float = typer.Option(0.85, "--threshold", "-t", help="Similarity threshold"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔄 Deduplicate cleaned data."""
    _setup_logging(verbose)
    from ..cleaner.pipeline import CleanPipeline  # noqa: F401 — ensure loaded

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Deduplicating...", total=None)
        # Use deduper.exact_dedup via run_full_pipeline stats, or standalone dedup_corpus
        from ..manager import dedup_corpus as do_dedup

        result = do_dedup()
        progress.update(task, completed=True)
        if "error" in result:
            rprint(f"[red]✗ {result['error']}[/red]")
        else:
            rprint(f"[green]✓ Deduplicated: {result['written']:,} unique / {result['scanned']:,} scanned[/green]")


# ============================================================
# ANALYZER
# ============================================================


@app.command()
def stats(
    corpus: str = typer.Option(None, "--corpus", "-c", help="Corpus path to analyze"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📊 Show corpus statistics."""
    _setup_logging(verbose)
    from ..analyzer.corpus import CorpusAnalyzer

    analyzer = CorpusAnalyzer()
    stats = analyzer.full_stats()

    table = Table(title="Zolai Corpus Statistics", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    for key, value in stats.items():
        if isinstance(value, float):
            table.add_row(key, f"{value:.2f}")
        elif isinstance(value, int):
            table.add_row(key, f"{value:,}")
        else:
            table.add_row(key, str(value))

    console.print(table)


@app.command()
def purity(
    corpus: str = typer.Option(None, "--corpus", "-c", help="Corpus path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔬 Run purity audit on corpus."""
    _setup_logging(verbose)
    from ..analyzer.corpus import CorpusAnalyzer

    analyzer = CorpusAnalyzer()
    results = analyzer.purity_audit()

    table = Table(title="Purity Audit Results", show_header=True)
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="green")

    for key, value in results.items():
        table.add_row(key, str(value))

    console.print(table)


# ============================================================
# TRAINER
# ============================================================


@app.command()
def train(
    prepare: bool = typer.Option(False, "--prepare", "-p", help="Prepare training data"),
    split: bool = typer.Option(True, "--split", "-s", help="Build train/val/test splits"),
    export: bool = typer.Option(False, "--export", "-e", help="Export to HuggingFace format"),
    val_ratio: float = typer.Option(0.02, "--val", help="Validation set ratio"),
    test_ratio: float = typer.Option(0.01, "--test", help="Test set ratio"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🏋️ Training data pipeline."""
    _setup_logging(verbose)
    from ..trainer.dataset import DatasetBuilder

    config.paths.ensure_dirs()
    builder = DatasetBuilder()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        if prepare:
            task = progress.add_task("Preparing training data...", total=None)
            result = builder.prepare_training()
            progress.update(task, completed=True)
            rprint(f"[green]✓ Prepared: {result}[/green]")

        if split:
            task = progress.add_task("Building splits...", total=None)
            result = builder.build_splits(val_ratio=val_ratio, test_ratio=test_ratio)
            progress.update(task, completed=True)
            rprint(f"[green]✓ Splits: {result}[/green]")

        if export:
            task = progress.add_task("Exporting...", total=None)
            result = builder.export_jsonl()
            progress.update(task, completed=True)
            rprint(f"[green]✓ Exported: {result}[/green]")


# ============================================================
# DICTIONARY
# ============================================================


@app.command()
def dictionary(
    query: str = typer.Option(None, "--search", "-s", help="Search term"),
    ingest: bool = typer.Option(False, "--ingest", "-i", help="Ingest dictionary files"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📖 Dictionary operations."""
    _setup_logging(verbose)

    manager = DictionaryManager()

    if ingest:
        dict_dir = config.paths.data_knowledge / "dictionaries"
        added = 0
        for path in sorted(dict_dir.glob("*.json*")):
            added += manager.ingest_json(str(path))
        rprint(f"[green]✓ Ingested {added} entries from {dict_dir}[/green]")

    if query:
        results = manager.search(query)
        if results:
            table = Table(title=f"Dictionary: '{query}'", show_header=True)
            table.add_column("Zolai", style="cyan")
            table.add_column("English", style="green")
            table.add_column("Source", style="magenta")
            table.add_column("Example", style="yellow")
            for entry in results[:20]:
                table.add_row(
                    entry.get("zolai", "—"),
                    entry.get("english", "—"),
                    entry.get("source", "—"),
                    (entry.get("example", "")[:80] + "...") if entry.get("example") else "",
                )
            console.print(table)
        else:
            rprint(f"[yellow]No results for '{query}'[/yellow]")
    else:
        rprint("[dim]Use --search <term> to look up words or --ingest to refresh data[/dim]")


# ============================================================
# BIBLE
# ============================================================


@app.command()
def bible(
    extract: bool = typer.Option(False, "--extract", "-e", help="Extract USX data"),
    parallel: bool = typer.Option(False, "--parallel", "-p", help="Build parallel pairs"),
    status: bool = typer.Option(True, "--status", "-s", help="Show Bible data status"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📕 Bible parallel data operations."""
    _setup_logging(verbose)

    bibles_dir = config.paths.data_knowledge / "bibles"
    if bibles_dir.exists():
        xml_files = list(bibles_dir.glob("**/*.xml"))
        rprint(f"[green]Bible data: {len(xml_files)} XML files in {bibles_dir}[/green]")
    else:
        rprint(f"[yellow]No Bible data found at {bibles_dir}[/yellow]")


# ============================================================
# INGEST (from zolai-dataset)
# ============================================================


@app.command()
def ingest(
    text: bool = typer.Option(False, "--text", "-t", help="Ingest text files from raw/text"),
    pdf: bool = typer.Option(False, "--pdf", "-p", help="Ingest PDFs from raw/pdf"),
    urls: str = typer.Option(None, "--urls", "-u", help="Comma-separated URLs to fetch"),
    name: str = typer.Option("web_crawl", "--name", "-n", help="Name for web crawl output"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📥 Ingest text, PDFs, or web pages into raw data."""
    _setup_logging(verbose)
    import asyncio

    from ..ingest import ingest_pdfs, ingest_text_files, ingest_web_pages

    config.paths.ensure_dirs()
    results = []

    if text:
        paths = ingest_text_files()
        results.append(f"Text: {len(paths)} files ingested")
        rprint(f"[green]✓ Ingested {len(paths)} text files[/green]")

    if pdf:
        path = ingest_pdfs()
        results.append(f"PDF: {path}")
        rprint(f"[green]✓ Extracted PDFs to {path}[/green]")

    if urls:
        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        path = asyncio.run(ingest_web_pages(url_list, name=name))
        results.append(f"Web: {len(url_list)} pages → {path}")
        rprint(f"[green]✓ Fetched {len(url_list)} web pages to {path}[/green]")

    if not results:
        rprint("[yellow]No ingest mode selected. Use --text, --pdf, or --urls[/yellow]")


# ============================================================
# MANAGER (from zolai-data-manager)
# ============================================================


@app.command()
def unify(
    root: str = typer.Option(None, "--root", "-r", help="Root directory to scan (default: data/)"),
    out: str = typer.Option(None, "--out", "-o", help="Output JSONL path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📦 Unify mixed JSON/JSONL/TXT files into one JSONL corpus."""
    _setup_logging(verbose)
    from ..manager import unify as do_unify

    root_path = Path(root) if root else None
    out_path = Path(out) if out else None
    result = do_unify(root=root_path, out=out_path)
    rprint(f"[green]✓ Unified {result['records']:,} records → {result['path']}[/green]")


@app.command()
def dedup_corpus(
    input_path: str = typer.Option(None, "--input", "-i", help="Input JSONL path"),
    out: str = typer.Option(None, "--out", "-o", help="Output JSONL path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔄 Deduplicate unified corpus by line content (SHA-256)."""
    _setup_logging(verbose)
    from ..manager import dedup_corpus as do_dedup

    in_p = Path(input_path) if input_path else None
    out_p = Path(out) if out else None
    result = do_dedup(input_path=in_p, out=out_p)
    if "error" in result:
        rprint(f"[red]✗ {result['error']}[/red]")
    else:
        rprint(f"[green]✓ Scanned {result['scanned']:,} → {result['written']:,} unique lines[/green]")


@app.command()
def filter_zolai(
    input_path: str = typer.Option(None, "--input", "-i", help="Input JSONL path"),
    out: str = typer.Option(None, "--out", "-o", help="Output JSONL path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🇿 Filter corpus to likely Zolai/Zomi text only."""
    _setup_logging(verbose)
    from ..manager import filter_zolai as do_filter

    in_p = Path(input_path) if input_path else None
    out_p = Path(out) if out else None
    result = do_filter(input_path=in_p, out=out_p)
    if "error" in result:
        rprint(f"[red]✗ {result['error']}[/red]")
    else:
        rprint(f"[green]✓ Scanned {result['scanned']:,} → {result['kept']:,} Zolai lines[/green]")


@app.command()
def status():
    """📈 Show unified corpus status (all stages)."""
    from ..manager import corpus_status

    data = corpus_status()

    table = Table(title="Corpus Pipeline Status", show_header=True)
    table.add_column("Stage", style="cyan")
    table.add_column("Lines", style="green", justify="right")
    table.add_column("Size", style="yellow", justify="right")
    table.add_column("Path", style="dim")

    for stage in ("unified", "dedup", "zolai_only"):
        info = data.get(stage, {})
        if info.get("exists"):
            size = info["size_bytes"]
            size_str = f"{size / 1_000_000:.1f} MB" if size > 1_000_000 else f"{size / 1_000:.1f} KB"
            table.add_row(stage, f"{info['lines']:,}", size_str, info["path"])
        else:
            table.add_row(stage, "—", "—", "(not found)")

    # Cleaned dir summary
    cleaned = data.get("cleaned_dir", {})
    if cleaned.get("exists"):
        table.add_row("cleaned/", f"{cleaned['lines']:,} files", "", cleaned["path"])

    console.print(table)


# ============================================================
# GUI & API
# ============================================================


@app.command()
def gui():
    """🖥️ Launch GTK3 GUI."""
    rprint("[bold green]Launching Zolai Toolkit GUI...[/bold green]")
    from ..gui.app import launch_gui

    launch_gui()


@app.command()
def api(
    host: str = typer.Option(None, "--host", "-h", help="Bind host"),
    port: int = typer.Option(None, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Auto-reload on changes"),
):
    """🌐 Start REST API server."""
    import uvicorn

    bind_host = host or config.api_host
    bind_port = port or config.api_port
    rprint(f"[bold green]Starting API on http://{bind_host}:{bind_port}[/bold green]")
    rprint(f"[dim]Docs: http://{bind_host}:{bind_port}/docs[/dim]")
    uvicorn.run("zolai_toolkit.api.server:app", host=bind_host, port=bind_port, reload=reload)


@app.command()
def chat(
    model: str = typer.Option(None, "--model", "-m", help="Ollama model"),
    host: str = typer.Option(None, "--host", "-h", help="API host"),
    port: int = typer.Option(None, "--port", "-p", help="API port"),
):
    """💬 Chat with Ollama model via API."""
    api_host = host or "http://localhost"
    api_port = port or 8300

    import httpx

    default_model = "qwen3-coder:480b-cloud"
    selected_model = model or default_model

    rprint("[bold green]🤖 Zolai Chat[/bold green]")
    rprint(f"[dim]Model: {selected_model}[/dim]")
    rprint(f"[dim]API: {api_host}:{api_port}[/dim]")
    rprint("Type /quit to exit, /clear to clear history\n")

    api_url = f"{api_host}:{api_port}"
    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except EOFError:
            rprint("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/quit", "/bye", "quit", "bye"):
            rprint("Bye!")
            break

        if user_input.lower() == "/clear":
            history = []
            rprint("History cleared.")
            continue

        if user_input.lower() == "/models":
            try:
                resp = httpx.get(f"http://{api_url}/chat/models", timeout=10)
                data = resp.json()
                rprint("[bold]Available models:[/bold]")
                for m in data.get("models", []):
                    rprint(f"  - {m['name']}")
            except Exception as e:
                rprint(f"[red]Error: {e}[/red]")
            continue

        try:
            resp = httpx.post(
                f"http://{api_url}/chat/chat",
                json={
                    "messages": [{"role": "user", "content": user_input}],
                    "model": selected_model,
                },
                timeout=60,
            )
            data = resp.json()
            response = data.get("message", {}).get("content", "")
            rprint(f"Assistant: {response}")

            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": response})
        except Exception as e:
            rprint(f"[red]Error: {e}[/red]")


# ============================================================
# OCR (Mistral Document AI)
# ============================================================


@app.command()
def ocr(
    input_path: str = typer.Argument(..., help="PDF file or directory of PDFs"),
    output: str = typer.Option(None, "--output", "-o", help="Output directory"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Skip already processed"),
    sleep: float = typer.Option(2.0, "--sleep", "-s", help="Seconds between requests"),
    table_format: str = typer.Option("html", "--table-format", help="Table format: html, markdown, none"),
    extract_header: bool = typer.Option(False, "--extract-header", help="Extract headers separately"),
    extract_footer: bool = typer.Option(False, "--extract-footer", help="Extract footers separately"),
    save_images: bool = typer.Option(False, "--save-images", help="Save extracted images"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📄 OCR PDFs using Mistral Document AI."""
    _setup_logging(verbose)
    from ..ocr.mistral_ocr import extract_markdown, extract_text, get_client, ocr_pdf, process_directory
    from ..shared.utils import ensure_dir

    inp = Path(input_path)
    if not inp.exists():
        rprint(f"[red]ERROR: {inp} not found[/red]")
        raise typer.Exit(1)

    out = Path(output) if output else inp.parent / "ocr-output"

    if inp.is_file() and inp.suffix.lower() == ".pdf":
        # Single file
        try:
            client = get_client()
            rprint(f"[cyan]OCR:[/cyan] {inp.name} ...", end="", flush=True)
            response = ocr_pdf(
                client, inp, table_format=table_format, extract_header=extract_header, extract_footer=extract_footer
            )
            md = extract_markdown(response)
            text = extract_text(response)
            ensure_dir(out)
            (out / f"{inp.stem}.md").write_text(md, encoding="utf-8")
            (out / f"{inp.stem}.txt").write_text(text, encoding="utf-8")
            pages = len(response.get("pages", []))
            rprint(f" [green]✓[/green] {pages} pages, {len(md):,} chars → {out}")
        except Exception as e:
            rprint(f" [red]✗ {e}[/red]")
            raise typer.Exit(1)
    elif inp.is_dir():
        # Directory
        try:
            client = get_client()
            stats = process_directory(client, inp, out, resume=resume, sleep=sleep, table_format=table_format)
            rprint("\n[bold]OCR Results:[/bold]")
            rprint(
                f"  Total: {stats['total']} | Success: {stats['success']} | "
                f"Failed: {stats['failed']} | Skipped: {stats['skipped']}"
            )
            rprint(f"  Pages: {stats['pages']} | Chars: {stats['chars']:,}")
            if stats["errors"]:
                rprint("\n[red]Errors:[/red]")
                for err in stats["errors"]:
                    rprint(f"  ✗ {err}")
        except Exception as e:
            rprint(f"[red]ERROR: {e}[/red]")
            raise typer.Exit(1)
    else:
        rprint(f"[red]ERROR: {inp} is not a PDF or directory[/red]")
        raise typer.Exit(1)


# ============================================================
# EVAL (offline quality metrics)
# ============================================================


@app.command()
def evaluate_cmd(
    set_name: str = typer.Option("smoke", "--set", help="Set name ('smoke') or a path/base prefix"),
    baseline: str = typer.Option(None, "--baseline", help="Path to baseline JSON (metric -> minimum floor)"),
    gate: bool = typer.Option(False, "--gate", help="Exit non-zero when any metric drops below its floor"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """📊 Run offline evaluation metrics on a dataset set."""
    from ..eval.cli import main as eval_main

    argv = ["--set", set_name]
    if baseline:
        argv += ["--baseline", baseline]
    if gate:
        argv += ["--gate"]
    if json_out:
        argv += ["--json"]
    raise typer.Exit(eval_main(argv))


# ============================================================
# FOUNDATION
# ============================================================


@asynccontextmanager
async def _foundation_analyzer(syllable_mode: str = "rule", tokenizer_model: str = None):
    """Context manager for FoundationAnalyzer."""
    from zolai.foundation import get_foundation_analyzer
    analyzer = get_foundation_analyzer(syllable_mode=syllable_mode, tokenizer_model=tokenizer_model)
    yield analyzer


@app.command()
def foundation(
    analyze: str = typer.Option(None, "--analyze", "-a", help="Analyze: word|sentence|paragraph"),
    text: str = typer.Option(None, "--text", "-t", help="Text to analyze"),
    syllable_mode: str = typer.Option("rule", "--syllable-mode", help="Syllable mode: rule|crf"),
    tokenizer_model: str = typer.Option(None, "--tokenizer-model", help="Path to tokenizer model"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔬 Foundation analysis: word, sentence, or paragraph."""
    _setup_logging(verbose)

    if not analyze or not text:
        rprint("[yellow]Usage: zolai foundation --analyze word|sentence|paragraph --text \"...\"[/yellow]")
        raise typer.Exit(1)

    from zolai.foundation import get_foundation_analyzer

    analyzer = get_foundation_analyzer(syllable_mode=syllable_mode, tokenizer_model=tokenizer_model)

    if analyze == "word":
        result = analyzer.analyze_word(text)
        _print_word_analysis(result)
    elif analyze == "sentence":
        result = analyzer.analyze_sentence(text)
        _print_sentence_analysis(result)
    elif analyze == "paragraph":
        result = analyzer.analyze_paragraph(text)
        _print_paragraph_analysis(result)
    else:
        rprint(f"[red]Unknown analysis type: {analyze}. Use word|sentence|paragraph[/red]")
        raise typer.Exit(1)


@app.command()
def foundation_gold_eval(
    syllable_mode: str = typer.Option("rule", "--syllable-mode", help="Syllable mode: rule|crf"),
    tokenizer_model: str = typer.Option(None, "--tokenizer-model", help="Path to tokenizer model"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📊 Run gold evaluation against ground-truth fixtures."""
    _setup_logging(verbose)
    from eval.gold_metrics import run_gold_evaluation

    report = run_gold_evaluation(
        syllable_mode=syllable_mode,
        tokenizer_model=tokenizer_model,
    )

    if json_out:
        import json
        out = json.dumps(report.to_dict(), indent=2)
        if output:
            Path(output).write_text(out, encoding="utf-8")
        else:
            rprint(out)
    else:
        report.print_summary()

    # Exit with non-zero if overall accuracy < 0.8
    if report.overall_accuracy < 0.8:
        raise typer.Exit(1)


# ============================================================
# FOUNDATION PIPELINE COMMANDS (Phase B)
# ============================================================


@app.command()
def foundation_ingest(
    source: str = typer.Option(..., "--source", "-s", help="Source type: web|pdf|bible|jsonl"),
    path: str = typer.Option(..., "--path", "-p", help="Path to source file/directory"),
    batch_size: int = typer.Option(1000, "--batch-size", "-b", help="Batch size for ingestion"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📥 Ingest raw corpus into foundation_raw_corpus."""
    _setup_logging(verbose)
    from pathlib import Path

    from zolai.pipeline.foundation import FoundationETL

    etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"Ingesting {source} from {path}...", total=None)
        result = etl.ingest_from_jsonl(Path(path), source_type=source)
        progress.update(task, completed=True)
        rprint(f"[green]✓ Ingested {result.records_processed} records[/green]")


@app.command()
def foundation_build(
    batch_size: int = typer.Option(1000, "--batch-size", "-b", help="Batch size for processing"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔨 Build staging layer from raw corpus using FoundationAnalyzer."""
    _setup_logging(verbose)
    from zolai.pipeline.foundation import FoundationETL

    etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Building staging layer...", total=None)
        result = etl.build_staging_from_raw()
        progress.update(task, completed=True)
        rprint(f"[green]✓ Built staging: {result.records_processed} staged, {result.errors} errors[/green]")


@app.command()
def foundation_promote(
    fact_type: str = typer.Option("word", "--fact-type", "-f", help="Fact type: word|sentence|paragraph"),
    threshold: float = typer.Option(0.9, "--threshold", "-t", help="Consensus threshold for promotion"),
    batch_size: int = typer.Option(1000, "--batch-size", "-b", help="Batch size for processing"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """⬆️ Promote staging to canonical with evidence gating."""
    _setup_logging(verbose)
    from zolai.pipeline.foundation import FoundationETL

    etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Promoting with threshold={threshold}...", total=None)
        result = etl.promote_staging_to_canonical(threshold=threshold, batch_size=batch_size)
        progress.update(task, completed=True)
        promoted = result.records_promoted
        queued = result.records_queued_for_review
        rprint(f"[green]✓ Promoted: {promoted} records, {queued} queued for review[/green]")


@app.command()
def foundation_verify(
    limit: int = typer.Option(0, "--limit", "-l", help="Max records to verify (0=all)"),
    batch_size: int = typer.Option(100, "--batch-size", "-b", help="Batch size"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """✅ Run batch verification on canonical records."""
    _setup_logging(verbose)
    from zolai.pipeline.foundation import FoundationETL

    etl = FoundationETL(db_path=f"sqlite:///{config.paths.db}")
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Running batch verification...", total=None)
        result = etl.run_verification(limit=limit, batch_size=batch_size)
        progress.update(task, completed=True)
        rprint(
            f"[green]✓ Verified: {result.records_processed} processed, "
            f"{result.records_promoted} promoted, {result.errors} errors[/green]"
        )


@app.command()
def foundation_review(
    status: str = typer.Option("pending", "--status", "-s", help="Filter: pending|resolved"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max items to show"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔍 Show the human review queue."""
    _setup_logging(verbose)
    from zolai.config import config
    from zolai.data.repositories import get_repositories

    repos = get_repositories(config.paths.db)
    review_queue = repos["foundation_review_queue"]

    if status == "pending":
        items = review_queue.get_pending(limit=limit)
    else:
        items = review_queue.get_pending(limit=limit)  # fallback

    table = Table(title=f"Review Queue ({status})", show_header=True)
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Fact Type", style="cyan")
    table.add_column("Fact Key", style="green")
    table.add_column("Priority", style="yellow", justify="right")
    table.add_column("Assignee", style="magenta")
    table.add_column("Created", style="dim")

    for item in items:
        table.add_row(
            str(item.get("id", "")),
            item.get("fact_type", ""),
            item.get("fact_key", ""),
            str(item.get("priority", "")),
            item.get("assignee", "") or "—",
            str(item.get("created_at", ""))[:19],
        )

    if not items:
        rprint("[yellow]No items in review queue[/yellow]")
    else:
        console.print(table)


@app.command()
def foundation_regression(
    category: str = typer.Option(
        None, "--category", "-c",
        help="Category: zvs|grammar|syllable|tone (default: all)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🧪 Run regression tests on linguistic engines."""
    _setup_logging(verbose)
    from zolai.foundation.regression import RegressionSuite

    categories = [category] if category else None
    suite = RegressionSuite(categories=categories)
    report = suite.run()

    if json_out:
        import json as _json
        rprint(_json.dumps(report.to_dict(), indent=2))
    else:
        report.print_summary()

    # Exit with non-zero if overall precision < 0.8
    if report.overall_precision < 0.8:
        raise typer.Exit(1)


@app.command()
def foundation_status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """📊 Show foundation table counts and pipeline status."""
    _setup_logging(verbose)
    from zolai.config import config
    from zolai.data.repositories import get_repositories
    repos = get_repositories(config.paths.db)
    table = Table(title="Foundation Pipeline Status", show_header=True)
    table.add_column("Layer", style="cyan")
    table.add_column("Table", style="yellow")
    table.add_column("Count", style="green", justify="right")

    # Raw
    raw_corpus = repos["foundation_raw_corpus"].count() if "foundation_raw_corpus" in repos else 0
    raw_llm = repos["foundation_raw_llm"].count() if "foundation_raw_llm" in repos else 0
    table.add_row("Raw", "foundation_raw_corpus", str(raw_corpus))
    table.add_row("", "foundation_raw_llm", str(raw_llm))

    # Staging
    staging_w = repos["foundation_staging_words"].count() if "foundation_staging_words" in repos else 0
    staging_s = repos["foundation_staging_sentences"].count() if "foundation_staging_sentences" in repos else 0
    staging_p = repos["foundation_staging_paragraphs"].count() if "foundation_staging_paragraphs" in repos else 0
    table.add_row("Staging", "foundation_staging_words", str(staging_w))
    table.add_row("", "foundation_staging_sentences", str(staging_s))
    table.add_row("", "foundation_staging_paragraphs", str(staging_p))

    # Canonical
    canon_w = repos["canonical_words"].count() if "canonical_words" in repos else 0
    canon_s = repos["canonical_sentences"].count() if "canonical_sentences" in repos else 0
    canon_p = repos["canonical_paragraphs"].count() if "canonical_paragraphs" in repos else 0
    table.add_row("Canonical", "canonical_words", str(canon_w))
    table.add_row("", "canonical_sentences", str(canon_s))
    table.add_row("", "canonical_paragraphs", str(canon_p))

    # Evidence/Consensus
    ev = repos["foundation_evidence"].count() if "foundation_evidence" in repos else 0
    ver = repos["foundation_verifications"].count() if "foundation_verifications" in repos else 0
    con = repos["foundation_consensus"].count() if "foundation_consensus" in repos else 0
    table.add_row("Evidence", "foundation_evidence", str(ev))
    table.add_row("", "foundation_verifications", str(ver))
    table.add_row("", "foundation_consensus", str(con))

    # Meta
    bat = repos["foundation_batches"].count() if "foundation_batches" in repos else 0
    rev = repos["foundation_review_queue"].count() if "foundation_review_queue" in repos else 0
    met = repos["foundation_metrics"].count() if "foundation_metrics" in repos else 0
    table.add_row("Meta", "foundation_batches", str(bat))
    table.add_row("", "foundation_review_queue", str(rev))
    table.add_row("", "foundation_metrics", str(met))

    console.print(table)


@app.command()
def foundation_review_web(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Auto-reload on changes"),
):
    """🌐 Launch the Foundation Review UI server."""
    import uvicorn

    rprint(f"[bold green]Starting Foundation Review UI on http://{host}:{port}[/bold green]")
    rprint(f"[dim]Review Queue: http://{host}:{port}/review/[/dim]")
    rprint(f"[dim]API Docs: http://{host}:{port}/docs[/dim]")
    uvicorn.run("zolai.api.server:app", host=host, port=port, reload=reload)


def _print_word_analysis(result) -> None:
    """Pretty-print word analysis."""
    table = Table(title=f"Word Analysis: {result.word}", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    tok = result.primary_token
    table.add_row("Syllables", " · ".join(s.syllable for s in tok.syllables))
    table.add_row("Syllable Count", str(tok.syllable_count))
    table.add_row("POS", f"{tok.pos.tag} (conf: {tok.pos.confidence:.2f})")
    table.add_row("Root", tok.morphology.root)
    table.add_row("Morphemes", " + ".join(tok.morphology.morphemes))
    table.add_row("Meaning", tok.morphology.meaning)
    table.add_row("Known Word", "✓" if result.is_known_word else "✗")
    table.add_row("ZVS Compliant", "✓" if result.zvs_compliant else "✗")
    if result.zvs_notes:
        table.add_row("ZVS Notes", "; ".join(result.zvs_notes))
    if result.dictionary_senses:
        table.add_row("Dict Senses", "; ".join(result.dictionary_senses))
    if result.bible_attestations:
        table.add_row("Bible Attestations", "; ".join(result.bible_attestations))

    console.print(table)


def _print_sentence_analysis(result) -> None:
    """Pretty-print sentence analysis."""
    table = Table(title="Sentence Analysis", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Text", result.sentence)
    table.add_row("Tokens", " | ".join(t.form for t in result.tokens))
    table.add_row("POS Tags", " | ".join(p.tag for p in result.pos_tags))
    table.add_row("SOV Valid", "✓" if result.sov_valid else "✗")
    table.add_row("Ergative Present", "✓" if result.ergative_present else "✗")
    table.add_row("Negation", result.negation_type or "—")
    table.add_row("Question Type", result.question_type or "—")
    table.add_row("Tense", result.tense or "—")
    table.add_row("ZVS Compliant", "✓" if result.zvs_compliant else "✗")
    if result.zvs_violations:
        table.add_row("ZVS Violations", "; ".join(result.zvs_violations))
    if result.grammar_patterns_matched:
        table.add_row("Grammar Patterns", ", ".join(result.grammar_patterns_matched))
    if result.bible_reference:
        table.add_row("Bible Ref", result.bible_reference)
    if result.english_translation:
        table.add_row("Translation", result.english_translation)

    console.print(table)


def _print_paragraph_analysis(result) -> None:
    """Pretty-print paragraph analysis."""
    table = Table(title="Paragraph Analysis", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Text", result.paragraph[:100] + ("..." if len(result.paragraph) > 100 else ""))
    table.add_row("Sentence Count", str(result.sentence_count))
    table.add_row("Total Tokens", str(result.total_tokens))
    table.add_row("Register", result.register)
    table.add_row("Dominant Tense", result.dominant_tense or "—")
    table.add_row("Cohesion Score", f"{result.cohesion_score:.2f}")
    table.add_row("Style Profile", ", ".join(f"{k}: {v:.0%}" for k, v in result.style_profile.items()))

    console.print(table)

    # Print sentence breakdown
    if result.sentences:
        sent_table = Table(title="Sentences", show_header=True)
        sent_table.add_column("#", style="dim")
        sent_table.add_column("Text", style="white")
        sent_table.add_column("Tense", style="cyan")
        sent_table.add_column("Negation", style="yellow")
        sent_table.add_column("Question", style="magenta")
        sent_table.add_column("ZVS", style="green")

        for i, s in enumerate(result.sentences, 1):
            sent_table.add_row(
                str(i),
                s.sentence[:80] + ("..." if len(s.sentence) > 80 else ""),
                s.tense or "—",
                s.negation_type or "—",
                s.question_type or "—",
                "✓" if s.zvs_compliant else "✗",
            )
        console.print(sent_table)


# ============================================================
# INFO
# ============================================================


@app.command()
def info():
    """ℹ️ Show toolkit configuration and paths."""
    config.paths.ensure_dirs()

    panel = Panel.fit(
        f"""[bold]Zolai Toolkit v1.0.0[/bold]

[cyan]Data Root:[/cyan]  {config.paths.data}
[cyan]Raw:[/cyan]        {config.paths.data_raw}
[cyan]Cleaned:[/cyan]    {config.paths.data_cleaned}
[cyan]Training:[/cyan]   {config.paths.data_training}
[cyan]Knowledge:[/cyan]  {config.paths.data_knowledge}
[cyan]Archive:[/cyan]    {config.paths.data_archive}
[cyan]Database:[/cyan]   {config.paths.db}

[cyan]API:[/cyan]        {config.api_host}:{config.api_port}
[cyan]GUI Theme:[/cyan]  {config.gui_theme}
""",
        title="🦜 Zolai Toolkit",
        border_style="green",
    )
    console.print(panel)


# ============================================================
# DATABASE
# ============================================================

db_app = typer.Typer(
    name="db",
    help="🗄️ Database maintenance (integrity checks).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(db_app, name="db")


@db_app.command("integrity")
def db_integrity(
    full: bool = typer.Option(
        False,
        "--full",
        help="Full PRAGMA integrity_check (tens of seconds on the 2.3GB store)",
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the raw report as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🧬 Check referential integrity (fast FK scan, or --full for a b-tree scan).

    The default scan is startup-safe. `--full` is explicit-only: it walks every
    table and index, so never wire it to a startup path. Both runs are recorded
    in `db_integrity_runs`. Exit code is 1 when violations are found.
    """
    _setup_logging(verbose)
    import json as _json

    from ..data.integrity import fk_policy, foreign_key_check, full_integrity_check

    report = full_integrity_check() if full else foreign_key_check(write=True)
    report = {**report, "fk_policy": fk_policy()}

    if json_out:
        rprint(_json.dumps(report, indent=2, default=str))
    else:
        label = "PRAGMA integrity_check" if full else "PRAGMA foreign_key_check"
        table = Table(title=f"Database integrity — {label}", border_style="blue")
        table.add_column("Check", style="cyan")
        table.add_column("Result")
        table.add_column("Issues", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Checked at (UTC)")
        table.add_column("FK policy")
        table.add_row(
            report["kind"],
            "[green]ok[/green]" if report["ok"] else "[red]FAILED[/red]",
            str(len(report["issues"])),
            f"{report['duration_ms']:.1f} ms",
            report["checked_at"],
            report["fk_policy"],
        )
        console.print(table)
        if report["issues"]:
            shown = Table(title="First violations", border_style="red")
            shown.add_column("Table")
            shown.add_column("Rowid")
            shown.add_column("Parent")
            shown.add_column("FK id")
            for issue in report["issues"][:20]:
                if isinstance(issue, dict):
                    shown.add_row(
                        str(issue.get("table", "—")),
                        str(issue.get("rowid", "—")),
                        str(issue.get("parent", "—")),
                        str(issue.get("fkid", "—")),
                    )
                else:  # full check returns PRAGMA messages
                    shown.add_row(str(issue), "", "", "")
            console.print(shown)
            if len(report["issues"]) > 20:
                rprint(f"[yellow]… {len(report['issues']) - 20} more[/yellow]")

    raise typer.Exit(code=0 if report["ok"] else 1)


# ============================================================
# API KEYS (ADR-014 / backlog P0-1)
# ============================================================

apikey_app = typer.Typer(
    name="apikey",
    help="🔑 API-key management for /api/v1 (issue, list, rotate, revoke).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(apikey_app, name="apikey")


def _apikey_bootstrap():
    """Ensure the api_keys table exists and return the auth service."""
    from ..api import auth

    auth.ensure_api_keys_table()
    return auth


def _apikey_print(payload: dict, plaintext: str | None = None) -> None:
    import json as _json

    if plaintext is not None:
        payload = {**payload, "plaintext": plaintext}
    rprint(_json.dumps(payload, indent=2, default=str))


@apikey_app.command("create")
def apikey_create(
    name: str = typer.Option(..., "--name", "-n", help="Human-readable key name"),
    scopes: str = typer.Option(
        ..., "--scopes", help='Comma-separated scopes, e.g. "dataset:read,catalog:read"'
    ),
    expires_days: int = typer.Option(
        0, "--expires-days", help="Expire after N days (0 = no expiry)"
    ),
    created_by: str = typer.Option("cli", "--created-by", help="Recorded on the key + audit"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON"),
):
    """🔐 Issue a new API key — the plaintext secret is shown **once**."""
    _setup_logging(False)
    auth = _apikey_bootstrap()
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
    try:
        key = auth.create_api_key(
            name=name,
            scopes=scope_list,
            created_by=created_by,
            expires_days=expires_days or None,
            actor=created_by,
        )
    except ValueError as exc:
        rprint(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1)

    if json_out:
        _apikey_print(key)
    else:
        table = Table(title="API key created", border_style="green")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Prefix")
        table.add_column("Scopes")
        table.add_column("Expires")
        table.add_row(
            str(key["id"]),
            key["name"],
            key["key_prefix"],
            ", ".join(key["scopes"]),
            key.get("expires_at") or "never",
        )
        console.print(table)
        rprint(f"[yellow]Plaintext (shown once, not stored):[/yellow] [bold]{key['plaintext']}[/bold]")


@apikey_app.command("list")
def apikey_list(
    json_out: bool = typer.Option(False, "--json", help="Print the raw records as JSON"),
):
    """📋 List API keys (hashes/prefixes only — never plaintext)."""
    _setup_logging(False)
    auth = _apikey_bootstrap()
    keys = auth.list_api_keys()

    if json_out:
        _apikey_print({"items": keys, "count": len(keys)})
    else:
        table = Table(title="API keys", border_style="blue")
        for column in ("ID", "Name", "Prefix", "Scopes", "Created", "Expires", "Last used", "Revoked"):
            table.add_column(column)
        for k in keys:
            table.add_row(
                str(k["id"]),
                k["name"],
                k["key_prefix"],
                ", ".join(k["scopes"]),
                k.get("created_at") or "",
                k.get("expires_at") or "never",
                k.get("last_used_at") or "",
                k.get("revoked_at") or "",
            )
        console.print(table)
        if not keys:
            rprint('[yellow]No API keys yet — run `zolai apikey create --name X --scopes "..."`[/yellow]')


@apikey_app.command("rotate")
def apikey_rotate(
    key_id: int = typer.Argument(..., help="ID of the key to rotate"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON"),
):
    """🔁 Rotate a key — a replacement is issued and the old one revoked."""
    _setup_logging(False)
    auth = _apikey_bootstrap()
    try:
        rotated = auth.rotate_api_key(key_id, actor="cli")
    except LookupError as exc:
        rprint(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1)
    except ValueError as exc:
        rprint(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1)

    if json_out:
        _apikey_print(rotated)
    else:
        rprint(f"[green]✓ Rotated key {rotated['old_id']}[/green] → new id {rotated['id']} "
               f"(old key revoked at {rotated['old_revoked_at']})")
        rprint(f"[yellow]New plaintext (shown once, not stored):[/yellow] [bold]{rotated['plaintext']}[/bold]")


@apikey_app.command("revoke")
def apikey_revoke(
    target: str = typer.Argument(..., help="Key ID or key prefix"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON"),
):
    """🚫 Revoke a key — it is rejected (401) from the very next request."""
    _setup_logging(False)
    auth = _apikey_bootstrap()
    record = auth.find_api_key(target)
    if record is None:
        rprint(f"[red]✗ No API key matches '{target}'[/red]")
        raise typer.Exit(code=1)
    try:
        revoked = auth.revoke_api_key(int(record["id"]), actor="cli")
    except ValueError as exc:
        rprint(f"[red]✗ {exc}[/red]")
        raise typer.Exit(code=1)

    if json_out:
        _apikey_print(revoked)
    else:
        rprint(f"[green]✓ Revoked key {revoked['id']}[/green] "
               f"({revoked['name']}, {revoked['key_prefix']}) at {revoked['revoked_at']}")


# ============================================================
# CORPUS CLEAN (C1 — audit + dry-run/apply clean)
# ============================================================

corpus_app = typer.Typer(
    name="corpus",
    help="🧹 C1 corpus audit (read-only) and ZVS/whitespace/HTML clean.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(corpus_app, name="corpus")


def _corpus_matrix(columns) -> Table:
    table = Table(title="Corpus defect matrix", border_style="blue")
    for head, justify in (
        ("Table", "left"), ("Column", "left"), ("Kind", "left"), ("Ctx", "left"),
        ("Cells", "right"), ("HTML", "right"), ("WS", "right"), ("ZVS", "right"),
        ("Suah", "right"), ("Sanity", "right"), ("Blocked", "right"),
        ("Changed", "right"), ("Would-write", "right"),
    ):
        table.add_column(head, justify=justify)
    for st in columns:
        table.add_row(
            st["table"], f'`{st["column"]}`', st["kind"], st["ctx"],
            str(st["cells"]), str(st["html"]), str(st["whitespace"]),
            str(st["zvs_cells"]), str(st["suah_cells"]), str(st["word_sanity"]),
            str(st["blocked"]), str(st["changed_cells"]),
            str(st["would_write"]) if st["writable"] else "— audit",
        )
    return table


def _corpus_needs_line(review: dict, label: str = "review-needs") -> str:
    return (
        f"[yellow]{label}:[/yellow] suah {review['suah']} · "
        f"word-sanity {review['word_sanity']} · json {review['json']} · "
        f"unique {review['unique']} "
        f"· total {review['total']}"
    )


@corpus_app.command("audit")
def corpus_audit(
    report: Path = typer.Option(None, "--report", help="Also write the markdown report to PATH"),
    table: str = typer.Option(None, "--table", "-t", help="Restrict the scan to one table"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw audit as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔎 Read-only corpus defect audit — never writes to the database."""
    _setup_logging(verbose)
    from ..data.corpus_clean import run_audit, write_report

    audit = run_audit(db_path=db, tables=[table] if table else None)

    if report is not None:
        path = write_report(audit, report)
        if not json_out:
            rprint(f"[green]✓ report →[/green] {path}")

    if json_out:
        import json as _json

        # Raw echo (not rich): audit lines can exceed the console width and
        # wrapping would corrupt the JSON payload.
        typer.echo(_json.dumps(audit, indent=2, default=str))
        raise typer.Exit(code=0)

    totals = audit["totals"]
    console.print(_corpus_matrix(audit["columns"]))
    rprint(_corpus_needs_line(audit["review_needs"]))
    rprint(
        f"[cyan]totals:[/cyan] cells {totals['cells']} · would-write {totals['would_write']} · "
        f"changed {totals['changed_cells']} · html {totals['html']} · ws {totals['whitespace']} · "
        f"zvs {totals['zvs_cells']} ({totals['zvs_hits']} hits)"
    )
    rprint(f"[cyan]duplicate groups:[/cyan] {audit['duplicate_groups_total']} "
           f"(counted only — dedupe is a founder decision)")
    if audit["skipped_tables"]:
        rprint(f"[yellow]skipped (not present):[/yellow] {', '.join(audit['skipped_tables'])}")
    rprint(f"[dim]{audit['scanned_tables'] and len(audit['scanned_tables'])} tables scanned in "
           f"{audit['duration_s']}s · db {audit['db']}[/dim]")


@corpus_app.command("clean")
def corpus_clean(
    table: str = typer.Option(None, "--table", "-t", help="Restrict to one table"),
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)"),
    batch_size: int = typer.Option(5000, "--batch-size", help="Rows per transaction"),
    state: Path = typer.Option(None, "--state", help="Resume-state file (default: next to db)"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw stats as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🧹 ZVS/whitespace/HTML clean — DRY-RUN unless `--apply` is given.

    Dry-run reports what would change and writes nothing (no database, no
    resume-state file). `--apply` batches updates transactionally, appends one
    `data_audit_log` row per changed cell, and persists a resume cursor.
    """
    _setup_logging(verbose)
    from ..data.corpus_clean import run_clean

    stats = run_clean(
        db_path=db,
        tables=[table] if table else None,
        apply=apply,
        batch_size=batch_size,
        state_path=state,
    )

    if json_out:
        import json as _json

        typer.echo(_json.dumps(stats, indent=2, default=str))
        raise typer.Exit(code=0)

    mode = "[bold green]APPLY[/bold green]" if apply else "[bold yellow]DRY-RUN[/bold yellow]"
    rprint(f"{mode} · batch {stats['batch_size']} · {stats['duration_s']}s · {stats['db']}")

    t = Table(title="Corpus clean — per table", border_style="blue")
    for head in ("Table", "Rows before", "Rows after", "Cells changed", "Audit rows", "HTML", "WS", "ZVS", "JSON"):
        t.add_column(head, justify="right" if head != "Table" else "left")
    for name, st in stats["tables"].items():
        t.add_row(
            name, str(st["rows_before"]), str(st["rows_after"]),
            str(st["cells_changed"]), str(st["audit_rows"]),
            str(st["classes"]["html"]), str(st["classes"]["whitespace"]),
            str(st["classes"]["zvs"]), str(st["classes"]["json"]),
        )
    console.print(t)

    totals = stats["totals"]
    rprint(_corpus_needs_line(totals["review_needs"],
                              label="left for review" if apply else "will stay untouched"))
    if apply:
        rprint(f"[cyan]written:[/cyan] {totals['cells_changed']} cells · "
               f"{totals['audit_rows']} audit rows · "
               f"log total {stats['audit_log_total_after']}")
        rprint(f"[dim]state → {stats['state_path']}[/dim]")

    if apply and totals["rows_before"] != totals["rows_after"]:
        rprint(f"[red]✗ row counts changed: {totals['rows_before']} → "
               f'{totals["rows_after"]} (must never happen)[/red]')
        raise typer.Exit(code=1)


@corpus_app.command("revert-c1")
def corpus_revert_c1(
    apply: bool = typer.Option(False, "--apply", help="Write reverts (default: dry-run)"),
    batch_size: int = typer.Option(5000, "--batch-size", help="Cells per transaction"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw stats as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """↩️ C1.1 correction — restore cells corpus_clean_v1 wrongly rewrote.

    Categories (derived from data_audit_log, never hard-coded ids):
    `name_ram` titlecase person-name cells · `en_headword` dictionary_en_zo EN
    glosses · `usage_ram` word_usage name lists · `grammar_meta` grammar
    teaching cells. DRY-RUN unless `--apply`. Original corpus_clean_v1 audit
    rows are kept; each reverted cell appends a `c1_1_name_revert by cli` row.
    """
    _setup_logging(verbose)
    from ..data.corpus_clean import REVERT_CATEGORIES, run_revert_c1_1

    stats = run_revert_c1_1(db_path=db, apply=apply, batch_size=batch_size)

    if json_out:
        import json as _json

        typer.echo(_json.dumps(stats, indent=2, default=str))
        raise typer.Exit(code=0)

    mode = "[bold green]APPLY[/bold green]" if apply else "[bold yellow]DRY-RUN[/bold yellow]"
    rprint(f"{mode} · C1.1 revert · {stats['duration_s']}s · {stats['db']}")
    rprint(
        f"[cyan]scanned:[/cyan] {stats['scanned_audit_rows']} corpus_clean_v1 audit rows · "
        f"out-of-scope {stats['status']['out_of_scope']} · "
        f"already {stats['status']['already']} · "
        f"conflict {stats['status']['conflict']} · missing {stats['status']['missing']}"
    )

    t = Table(title="C1.1 revert — by category", border_style="blue")
    t.add_column("Category", justify="left")
    t.add_column("Detected", justify="right")
    t.add_column("Reverted", justify="right")
    for key in REVERT_CATEGORIES:
        t.add_row(key, str(stats["detected"][key]), str(stats["reverted_by_category"][key]))
    t.add_row("TOTAL", str(stats["status"]["pending"]), str(stats["reverted"]))
    console.print(t)

    rprint(f"[cyan]audit rows appended:[/cyan] {stats['audit_rows']} "
           f"· log total {stats['audit_log_total_after']}")
    rprint(f"[cyan]pending after run:[/cyan] {stats['pending_after']}"
           + (" [green]✓ (idempotent)[/green]"
              if apply and stats["pending_after"] == 0 else ""))

    if apply and stats["row_counts_before"] != stats["row_counts_after"]:
        rprint(f"[red]✗ row counts changed: {stats['row_counts_before']} → "
               f'{stats["row_counts_after"]} (must never happen)[/red]')
        raise typer.Exit(code=1)


# ============================================================
# BIBLE REF FIX (C2 — audit + archive/fix + revert + remap)
# ============================================================

bible_ref_app = typer.Typer(
    name="bible-ref",
    help="📖 Bible verse ref audit (read-only), archive+fix, revert and downstream remap.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(bible_ref_app, name="bible-ref")


def _bible_ref_classes(classes: dict) -> Table:
    table = Table(title="Bible ref classification", border_style="blue")
    table.add_column("Class", justify="left")
    table.add_column("Rows", justify="right")
    for name, value in classes.items():
        table.add_row(f"`{name}`", str(value))
    return table


def _bible_ref_needs_line(counts: dict) -> str:
    inner = " · ".join(f"{k} {v}" for k, v in counts.items() if v)
    return f"[yellow]needs-founder (report only):[/yellow] {inner or 'none'}"


@bible_ref_app.command("audit")
def bible_ref_audit(
    report: Path = typer.Option(None, "--report", help="Also write the markdown report to PATH"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    kjv: Path = typer.Option(None, "--kjv", help="KJV JSON ground truth (default: data/reference/kjv_en.json)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw audit as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔎 Read-only verse-ref audit — never writes (not even DDL)."""
    _setup_logging(verbose)
    from ..data.bible_ref_fix import run_audit, write_report

    audit = run_audit(db_path=db, kjv_path=kjv)

    if report is not None:
        path = write_report(audit, report)
        if not json_out:
            rprint(f"[green]✓ report →[/green] {path}")

    if json_out:
        import json as _json

        typer.echo(_json.dumps(audit, indent=2, default=str))
        raise typer.Exit(code=0)

    console.print(_bible_ref_classes(audit["classes"]))
    rprint(_bible_ref_needs_line(audit["needs_founder_counts"]))
    rprint(
        f"[cyan]structure:[/cyan] rows {audit['db_rows']} · ref-formula mismatch "
        f"{audit['ref_formula_mismatches']} · duplicate triples {audit['duplicate_ref_triples']} · "
        f"target {audit['target_row_count']}"
    )
    rprint(
        "[cyan]pairs:[/cyan] " + " · ".join(f"{k} {v}" for k, v in audit["pair_quality"].items())
        + f" · EN-restorable {audit['en_restore_candidates']}"
    )
    rprint(
        "[cyan]downstream:[/cyan] "
        + " · ".join(f"{k} {v['bad_refs']}/{v['rows']}" for k, v in audit["downstream"].items())
    )
    rprint(
        f"[cyan]kjv:[/cyan] {'loaded' if audit['kjv_loaded'] else 'MISSING (structural-only)'} · "
        f"archive rows {audit['archive']['count']}"
    )
    rprint(f"[dim]scanned {audit['db_rows']} rows in {audit['duration_s']}s · db {audit['db']}[/dim]")


@bible_ref_app.command("fix")
def bible_ref_fix(
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)"),
    fill_null_en: bool = typer.Option(False, "--fill-null-en", help="Founder-gated: fill NULL en_kJV from KJV"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    kjv: Path = typer.Option(None, "--kjv", help="KJV JSON ground truth (default: data/reference/kjv_en.json)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw stats as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🗄️ Archive + remove impossible refs — DRY-RUN unless `--apply`.

    Per impossible ref: resolve the (chapter-1, verse) target, copy the
    KJV-verified EN into a demonstrably-wrong target cell, copy the full row
    into `bible_verses_archive`, audit it and DELETE it from the canonical
    table. No ZO/label writes; needs-founder rows stay untouched.
    """
    _setup_logging(verbose)
    from ..data.bible_ref_fix import run_fix

    stats = run_fix(db_path=db, kjv_path=kjv, apply=apply, fill_null_en=fill_null_en)

    if json_out:
        import json as _json

        typer.echo(_json.dumps(stats, indent=2, default=str))
        raise typer.Exit(code=0)

    mode = "[bold green]APPLY[/bold green]" if apply else "[bold yellow]DRY-RUN[/bold yellow]"
    rprint(f"{mode} · bible ref fix · {stats['duration_s']}s · {stats['db']}")
    rprint(
        f"[cyan]rows:[/cyan] {stats['rows_before']} → {stats['rows_after']} "
        f"(target {stats['row_target']}) · actions {stats['actions']}/"
        f"{stats['actions_pending']} · already {stats['already_archived']} · "
        f"refused {stats['refused_duplicate']}"
    )
    if apply:
        writes = stats["archived"] + stats["en_restored"] + stats["fill_null_en"]
        rprint(
            f"[cyan]written:[/cyan] archived {stats['archived']} · EN restored "
            f"{stats['en_restored']} · NULL filled {stats['fill_null_en']} · "
            f"audit rows {stats['audit_rows_written']} · archive total "
            f"{stats['archive_count']}"
        )
        if stats["audit_rows_written"] != writes:
            rprint("[red]✗ audit rows != writes[/red]")
            raise typer.Exit(code=1)
        if stats["rows_before"] - stats["rows_after"] != stats["archived"]:
            rprint("[red]✗ row delta != archived count[/red]")
            raise typer.Exit(code=1)
    else:
        rprint(
            f"[cyan]would archive:[/cyan] {stats['actions']} rows · EN restores "
            f"{stats['en_restore_candidates']}"
        )
    rprint(_bible_ref_needs_line(stats["needs_founder_counts"]))
    if apply and stats["unresolvable"]:
        rprint(
            f"[yellow]unresolvable (archived, no target to restore):[/yellow] "
            f"{stats['unresolvable']}"
        )


@bible_ref_app.command("revert")
def bible_ref_revert(
    apply: bool = typer.Option(False, "--apply", help="Write reverts (default: dry-run)"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw stats as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """↩️ Reinsert archived rows byte-identically and undo EN restores.

    DRY-RUN unless `--apply`. Reinserts by `source_row_id` (refuses any id/ref
    that is already live), reverses KJV-verified EN restores from the audit
    log and appends `bible_ref_fix_revert by cli` rows — the original
    `bible_ref_fix_v1` rows are kept.
    """
    _setup_logging(verbose)
    from ..data.bible_ref_fix import run_revert

    stats = run_revert(db_path=db, apply=apply)

    if json_out:
        import json as _json

        typer.echo(_json.dumps(stats, indent=2, default=str))
        raise typer.Exit(code=0)

    mode = "[bold green]APPLY[/bold green]" if apply else "[bold yellow]DRY-RUN[/bold yellow]"
    rprint(f"{mode} · bible ref revert · {stats['duration_s']}s · {stats['db']}")
    rprint(
        f"[cyan]archive:[/cyan] {stats['archive_rows']} rows · restored "
        f"{stats['restored']} · refused {stats['refused_duplicate']} · "
        f"EN reversals {stats['en_restores_reversed']}/{stats['en_restores_seen']} "
        f"(conflicts {stats['en_restores_conflict']})"
    )
    if apply:
        rprint(f"[cyan]audit rows appended:[/cyan] {stats['audit_rows_written']}")
        rprint(f"[cyan]archive left:[/cyan] {stats['archive_rows_after']}")


@bible_ref_app.command("remap")
def bible_ref_remap(
    apply: bool = typer.Option(False, "--apply", help="Write changes (default: dry-run)"),
    db: Path = typer.Option(None, "--db", help="SQLite store (default: canonical data/zolai.db)"),
    json_out: bool = typer.Option(False, "--json", help="Print the raw stats as JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """🔗 Phase 2 — remap archived→target refs in word_alignments/translations.

    DRY-RUN unless `--apply`. Every rewritten row gets a
    `bible_ref_fix_v1: downstream_remap by cli` audit row.
    """
    _setup_logging(verbose)
    from ..data.bible_ref_fix import run_remap

    stats = run_remap(db_path=db, apply=apply)

    if json_out:
        import json as _json

        typer.echo(_json.dumps(stats, indent=2, default=str))
        raise typer.Exit(code=0)

    mode = "[bold green]APPLY[/bold green]" if apply else "[bold yellow]DRY-RUN[/bold yellow]"
    rprint(f"{mode} · bible ref remap · {stats['duration_s']}s · {stats['db']}")
    rprint(f"[cyan]mapping:[/cyan] {stats['mapping_size']} archived refs")
    for table, info in stats["downstream"].items():
        rprint(f"[cyan]{table}:[/cyan] {info['remapped']} remapped of {info['scanned']} scanned")
    if apply:
        rprint(f"[cyan]audit rows appended:[/cyan] {stats['audit_rows_written']}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()


# ============================================================
# SERVER + DESKTOP
# ============================================================


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
    reload: bool = typer.Option(False, "--reload", "-r"),
):
    """🌐 Start the Zolai API server."""
    import subprocess
    import sys

    rprint(f"[green]Starting API server on {host}:{port}...[/green]")
    cmd = [sys.executable, "-m", "uvicorn", "zolai.api.server:app",
           "--host", host, "--port", str(port)]
    if reload:
        cmd.append("--reload")
    subprocess.run(cmd, cwd=str(config.paths.data.parent))


@app.command()
def desktop():
    """🖥️ Launch the Zolai desktop app."""
    import os
    import subprocess

    workspace = Path(__file__).parent.parent.parent.parent
    tauri_dir = workspace / "zolai-tauri"
    api_url = f"http://localhost:{config.api_port}"

    # 1. Check if API is running
    import urllib.request
    try:
        urllib.request.urlopen(f"{api_url}/health", timeout=2)
        rprint("[green]✅ API already running[/green]")
    except Exception:
        rprint("[yellow]Starting API server...[/yellow]")
        subprocess.Popen(
            [os.sys.executable, "-m", "uvicorn", "zolai.api.server:app",
             "--host", "0.0.0.0", "--port", str(config.api_port)],
            cwd=str(config.paths.data.parent),
            stdout=open("/tmp/zolai-api.log", "w"),
            stderr=subprocess.STDOUT,
        )
        import time
        time.sleep(2)
        rprint(f"[green]✅ API started on :{config.api_port}[/green]")

    # 2. Launch desktop app
    bin_path = tauri_dir / "src-tauri/target/release/zolai-desktop"
    if bin_path.exists():
        rprint("[green]Launching compiled desktop app...[/green]")
        subprocess.Popen([str(bin_path)])
    else:
        rprint("[yellow]No compiled binary — building and running dev mode...[/yellow]")
        subprocess.Popen(["bunx", "tauri", "dev"], cwd=str(tauri_dir))
