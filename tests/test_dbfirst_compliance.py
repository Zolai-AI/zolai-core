"""Database-first (DB-first) compliance enforcement.

Guarantees that the Zolai runtime serving layer reads canonical corpus data
exclusively from ``data/zolai.db`` — never from raw JSONL corpora.

Three gates:

1. ``clean-serving-modules`` — modules that were converted to DB reads must
   contain **zero** ``.jsonl`` references anywhere in their source.
2. ``allowlist`` — every ``zolai/**/*.py`` file that still mentions ``.jsonl``
   must be in the explicit allowlist (ingest / build / export / session-state /
   metadata-string only, never a runtime serving read).
3. ``functional`` — the DB serving layer answers the corpus reads it did from
   JSONL; a mismatched count means a mis-wired repository.

Run: ``.venv/bin/python -m pytest tests/test_dbfirst_compliance.py -q``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zolai.data.repositories import get_engine

PACKAGE = Path(__file__).resolve().parents[1] / "zolai"

# ---------------------------------------------------------------------------
# Gate 1 — fully clean serving modules (no .jsonl at all).
# ---------------------------------------------------------------------------
CLEAN_MODULES = [
    "api/rag_context.py",
    "api/rag_context_v2.py",
    "api/accuracy_scorer.py",
    "learning/sentence_validator.py",
    "learning/word_attestation.py",
    "learning/context_validator.py",
    "learning/bible_pattern_learner.py",
    "knowledge/rag_contract.py",
]


@pytest.mark.parametrize("rel", CLEAN_MODULES)
def test_clean_serving_modules_have_no_jsonl(rel: str) -> None:
    """Converted serving modules must not reference JSONL at all."""
    src = (PACKAGE / rel).read_text(encoding="utf-8")
    assert ".jsonl" not in src, f"{rel} must be JSONL-free (serving read):\n{src}"


# ---------------------------------------------------------------------------
# Gate 2 — global allowlist for every remaining .jsonl reference.
# ---------------------------------------------------------------------------
# Modules that legitimately touch .jsonl (ingest/build/export/test/session-state
# or metadata strings). Anything outside this list is a serving read regression.
ALLOWLIST_RELS = {
    "api/jsonl_router.py",
    "api/learning_engine.py",  # live session/user-state store
    "api/memory_layers.py",  # live session/user-state store
    "api/pipeline.py",  # export + corpus_add community staging append
    "api/server.py",  # metadata strings + jsonl_router import
    "bible/extractor.py",  # build
    "core/ingest_kaggle.py",  # ingest
    "core/jsonl_pipeline.py",  # ingest
    "core/jsonl_pipeline_v2.py",  # ingest
    "core/jsonl_pipeline_v3.py",  # ingest
    "data/database.py",  # validation error-message string only
    "data/export.py",  # export filename map
    "data/migrate.py",  # one-time migration source filename map
    "data/models.py",  # source-name docstrings
    "data/schemas.py",  # schemas + docstrings
    "data/repositories/extended.py",  # docstrings (old jsonl shape mirrors)
    "dictionary/manager.py",  # export_jsonl build path only
    "eval/datasets.py",  # eval fixtures
    "knowledge/__init__.py",  # docstring only
    "trainer/__init__.py",  # trainer dataset build/export (train/val/test jsonl)
    "knowledge/ingest.py",  # export bundle artifact
    "knowledge/ngram.py",  # export bundle artifact
    "knowledge/pdf.py",  # PDF ingest
    "knowledge/retrieve.py",  # one-time HF ingest/export bootstrap
    "learning/feedback.py",  # live user-correction store
    "manager/__init__.py",  # unified-corpus build utilities
    "zvs/cli.py",  # CLI
    "trainer/dataset.py",  # trainer dataset build/export
    "trainer/training_dataset_builder.py",  # export_to_hf artifact
    "api/desktop_app.py",  # mentions jsonl in docs/comments / offline export paths
    "foundation/etl.py",  # foundation ingest (build-time)
    "pipeline/foundation.py",  # foundation ingest (build-time)
}
ALLOWLIST_PREFIXES = (
    "analyzer/",  # corpus build
    "cleaner/",  # cleaning pipeline
    "crawler/",  # crawl
    "morphology/",  # training ingests
    "pos_tagger/",  # training ingests
    "syllable/",  # annotation / tokenizer training
)


def _all_zolai_py_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_every_jsonl_reference_is_allowlisted() -> None:
    """No runtime module outside the allowlist may reference JSONL."""
    offenders: list[str] = []
    for path in _all_zolai_py_files():
        rel = str(path.relative_to(PACKAGE))
        if rel in ALLOWLIST_RELS:
            continue
        if rel.startswith(ALLOWLIST_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ".jsonl" in text:
            offenders.append(rel)
    assert not offenders, (
        "Modules reference .jsonl but are not allowlisted (possible serving "
        f"read regression): {offenders}"
    )


# ---------------------------------------------------------------------------
# Gate 3 — functional: the DB repositories back the corpus reads DB-first.
# ---------------------------------------------------------------------------

def test_repository_layer_reads_canonical_database() -> None:
    """The canonical DB carries the corpus the serving layer depends on."""
    from zolai.dictionary.manager import DictionaryManager
    from zolai.learning.word_attestation import get_word_attestation

    dm = DictionaryManager()
    assert dm.count > 80_000, f"dictionary reads were not DB-backed: {dm.count}"

    wa = get_word_attestation()
    assert wa.attest_word("pasian")["confidence"] in {"VERIFIED", "HIGH"}


def test_ngram_and_active_rows_backed_by_db() -> None:
    """ngram repo + versioning helpers resolve against the live database."""
    from zolai.data.repositories.extended import NgramRepository
    from zolai.data.versioning import active_version

    ngram = NgramRepository(get_engine())
    # as_tables() must not raise when the ngram table is empty or populated.
    assert isinstance(ngram.as_tables(), dict)

    versions = active_version()
    assert "dictionary" in versions
    assert versions["dictionary"] >= 1
