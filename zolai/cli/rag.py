"""CLI commands for RAG (Phase 6)."""

from __future__ import annotations

import json
import logging
from typing import Any

import typer
from sqlalchemy.engine import Engine

from zolai.data.repositories import get_engine
from zolai.rag import build_knowledge_vectors
from zolai.rag.retrieve import UnifiedRetriever
from zolai.knowledge import KGRepository

log = logging.getLogger(__name__)
app = typer.Typer(name="rag", help="RAG commands (Phase 6)")


def _get_db_engine() -> Engine:
    return get_engine()


@app.command("build")
def build(
    sources: list[str] = typer.Option(None, "--source", "-s", help="Sources to embed"),
    limit: int = typer.Option(None, "--limit", help="Max rows per source"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing"),
) -> None:
    """Build knowledge vectors from canonical sources."""
    engine = _get_db_engine()
    result = build_knowledge_vectors(
        engine,
        sources=sources if sources else None,
        limit=limit,
        dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("word")
def word(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(20, "--limit", help="Max evidence items"),
) -> None:
    """Get full evidence pack for a word (§20)."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    pack = retriever.query_word(word, limit)
    print(json.dumps({
        "word": pack.word,
        "forms": pack.forms,
        "frequency": pack.frequency,
        "document_frequency": pack.document_frequency,
        "sentence_frequency": pack.sentence_frequency,
        "pos": pack.pos,
        "morphology": pack.morphology,
        "examples": pack.examples,
        "collocations": pack.collocations,
        "grammar_usage": pack.grammar_usage,
        "sources": pack.sources,
        "confidence": pack.confidence,
    }, indent=2, ensure_ascii=False))


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(20, "--limit", help="Max results"),
    source_filter: list[str] = typer.Option(None, "--source", help="Filter sources"),
) -> None:
    """Hybrid search (lexical + vector)."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    results = retriever.search(query, limit, source_filter)
    print(json.dumps([r.__dict__ for r in results], indent=2, ensure_ascii=False))


@app.command("rag")
def rag_query(
    question: str = typer.Argument(..., help="Question to answer"),
    limit: int = typer.Option(5, "--limit", help="Max retrieval results"),
) -> None:
    """Full RAG: question → retrieval → answer with citations."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    result = retriever.rag_query(question, limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("word-forms")
def word_forms(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(20, "--limit", help="Max forms"),
) -> None:
    """Get word forms."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    forms = retriever.query_word_forms(word, limit)
    print(json.dumps({"word": word, "forms": forms}, indent=2, ensure_ascii=False))


@app.command("contexts")
def contexts(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(20, "--limit", help="Max contexts"),
) -> None:
    """Get contexts for a word."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    ctx = retriever.query_word_contexts(word, limit)
    print(json.dumps({"word": word, "contexts": ctx}, indent=2, ensure_ascii=False))


@app.command("collocations")
def collocations(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(20, "--limit", help="Max collocations"),
) -> None:
    """Get collocations for a word."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    collocs = retriever.query_word_collocations(word, limit)
    print(json.dumps({"word": word, "collocations": collocs}, indent=2, ensure_ascii=False))


@app.command("patterns")
def patterns(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(20, "--limit", help="Max patterns"),
) -> None:
    """Get grammar patterns for a word."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    pats = retriever.query_word_patterns(word, limit)
    print(json.dumps({"word": word, "patterns": pats}, indent=2, ensure_ascii=False))


@app.command("evidence")
def evidence(
    word: str = typer.Argument(..., help="Word to query"),
    limit: int = typer.Option(50, "--limit", help="Max evidence items"),
) -> None:
    """Get evidence chain for a word."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    ev = retriever.query_word_evidence(word, limit)
    print(json.dumps({"word": word, "evidence": ev}, indent=2, ensure_ascii=False))


@app.command("stats")
def stats() -> None:
    """Get knowledge statistics."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    stats = retriever.get_knowledge_statistics()
    print(json.dumps(stats, indent=2, ensure_ascii=False))


@app.command("version")
def version() -> None:
    """Get knowledge version."""
    engine = _get_db_engine()
    retriever = UnifiedRetriever(engine)
    ver = retriever.get_knowledge_version()
    print(json.dumps(ver, indent=2, ensure_ascii=False))
