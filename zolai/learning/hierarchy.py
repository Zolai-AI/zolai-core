"""Hierarchical corpus learning: line → paragraph → document → corpus.

Every Zolai source we hold is studied at four levels, using the same engine:

- **line** — one verse (Bible) or one sentence (articles, wiki);
- **paragraph** — explicit paragraphs where the source has them (``<p>``
  blocks in articles, blank-line blocks in wiki), learned boundaries
  (TextTiling over TF-IDF cosine) where it does not (Bible chapters);
- **document** — one book, article, lesson, or wiki page;
- **corpus** — one source as a whole.

Nothing here is a hardcoded grammar list. Function words are down-weighted by
IDF, paragraph boundaries come from a cosine dip relative to the chapter's own
depth distribution, and paragraph openers are *learned* by comparing how often
a token starts a paragraph against how often it starts a line (lift). The only
constants are the statistical thresholds, and each is documented below.

Writes are idempotent:

- ``corpus_learning_layers`` — upserted on ``(source, level, unit_id)``, with
  stale paragraph rows removed in the same transaction;
- ``grammar_patterns`` — learned openers under ``disc_p_<source>_<marker>``
  with ``book='PARAGRAPH_OPENER'``, upserted by ``pattern_id``;
- ``foundation_evidence`` — one tier-1/4 row per opener, linked back into the
  pattern's ``evidence_ids``.

No model calls, no network, no ``hash()`` ids: a second run changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Any, Iterator, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from zolai.learning.bible_pattern_learner import classify_clause, extract_structure
from zolai.learning.discovery.evidence import EVIDENCE_TIER_MAP, upsert_evidence_bulk
from zolai.shared.text import tokenize_words

# ── tunable constants (documented, not magic) ───────────────────────────────

#: A boundary needs a cosine dip of at least this much (absolute floor).
BOUNDARY_MIN_DEPTH = 0.04
#: A boundary must also exceed the chapter's own mean depth by this many σ.
BOUNDARY_SIGMA = 0.5
#: Never emit a paragraph shorter than this many lines.
MIN_PARAGRAPH_LINES = 2
#: An explicit paragraph longer than this is re-segmented with TextTiling.
MAX_PARAGRAPH_LINES = 12
#: A document needs at least this many tokens to teach anything.  Short
#: lesson stories are single sentences (~12 tokens), so this only drops
#: navigation crumbs, not real Zolai text.
MIN_DOCUMENT_TOKENS = 10
#: Openers: minimum paragraph-initial count, minimum share of all paragraphs,
#: and lift over the corpus rate.  The share floor keeps a large corpus from
#: admitting thousands of Zipf-tail bigrams that clear a bare count gate.
OPENER_MIN_COUNT = 5
OPENER_MIN_SHARE = 0.001
OPENER_MIN_LIFT = 2.0
#: Lift is mapped to (0, 1] confidence by dividing by this value.
OPENER_LIFT_FULL = 10.0
#: How many terms per unit are kept in the stored features.
LINE_TERMS = 5
PARAGRAPH_TERMS = 12
DOCUMENT_TERMS = 25
CORPUS_TERMS = 40
SAMPLE_LIMIT = 8

DEFAULT_LEVELS: tuple[str, ...] = ("line", "paragraph", "document", "corpus")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[\"'“”‘’(\[]*[A-ZÀ-Þ])")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── source specifications ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceSpec:
    """How to read one source into (document → group → lines)."""

    name: str
    kind: str  # 'verses' | 'documents'
    table: str
    tier: int
    where: str = ""
    id_col: str = "id"
    title_col: str = "title"
    text_col: str = "content"
    alt_col: str = ""
    link_col: str = ""
    book_col: str = ""
    chapter_col: str = ""
    verse_col: str = ""
    ref_col: str = ""
    format: str = "html"  # 'html' | 'markdown' | 'text'
    label: str = ""


SOURCE_SPECS: dict[str, SourceSpec] = {
    "bible": SourceSpec(
        name="bible",
        kind="verses",
        table="bible_verses",
        tier=1,
        where="zo_tdb77 IS NOT NULL AND trim(zo_tdb77) != ''",
        id_col="id",
        text_col="zo_tdb77",
        alt_col="en_kJV",
        book_col="book",
        chapter_col="chapter",
        verse_col="verse",
        ref_col="ref",
        label="Bible (TDB77)",
    ),
    "zomidaily": SourceSpec(
        name="zomidaily",
        kind="documents",
        table="articles",
        tier=4,
        where=(
            "namespace = 'news' AND language = 'zolai' "
            "AND content IS NOT NULL AND trim(content) != ''"
        ),
        title_col="title",
        text_col="content",
        link_col="link",
        format="html",
        label="Zomidaily news",
    ),
    "lessons": SourceSpec(
        name="lessons",
        kind="documents",
        table="articles",
        tier=4,
        where=(
            "namespace = 'lessons' AND content IS NOT NULL AND trim(content) != ''"
        ),
        title_col="title",
        text_col="content",
        link_col="link",
        format="html",
        label="Zolai lessons & songs",
    ),
    "wiki": SourceSpec(
        name="wiki",
        kind="documents",
        table="wiki_content",
        tier=4,
        # Vocabulary pages are generated from dict_master: learning openers
        # from them would feed dictionary facts back in as corpus evidence.
        where=(
            "wiki_category NOT IN ('other', 'vocabulary') "
            "AND content IS NOT NULL AND trim(content) != ''"
        ),
        title_col="title",
        text_col="content",
        link_col="source_path",
        format="markdown",
        label="Zolai wiki",
    ),
}


# ── intermediate structures ────────────────────────────────────────────────


@dataclass
class Line:
    """One studyable line: a verse or a sentence."""

    ref: str
    text: str
    alt: str = ""


@dataclass
class DocUnit:
    """One document with either explicit paragraphs or one implicit group."""

    source: str
    doc_id: str
    title: str
    group_id: str
    lines: list[Line]
    explicit_paragraphs: list[list[Line]] | None = None


@dataclass
class LayerRow:
    """One row destined for ``corpus_learning_layers``."""

    source: str
    level: str
    unit_id: str
    doc_id: str
    group_id: str
    ref: str
    features: dict[str, Any]
    confidence: float

    @property
    def features_json(self) -> str:
        return json.dumps(self.features, ensure_ascii=False, sort_keys=True)

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.features_json.encode("utf-8")).hexdigest()[:16]


@dataclass
class OpenerCorpus:
    """Corpus-wide unigram/bigram counts — the baseline an opener is lifted over.

    The counts include the paragraph-initial occurrences themselves, so
    ``count / slots`` is deflated rather than zero and the lift never divides
    by an absent baseline.
    """

    unigrams: Counter = field(default_factory=Counter)
    bigrams: Counter = field(default_factory=Counter)
    unigram_slots: int = 0
    bigram_slots: int = 0

    def add_tokens(self, tokens: Sequence[str]) -> None:
        self.unigrams.update(tokens)
        self.bigrams.update(
            " ".join(tokens[index : index + 2]) for index in range(len(tokens) - 1)
        )
        self.unigram_slots += len(tokens)
        self.bigram_slots += max(0, len(tokens) - 1)


@dataclass
class SourceResult:
    """Per-source counters gathered while learning."""

    documents: int = 0
    lines: int = 0
    paragraphs: int = 0
    tokens: int = 0
    skipped_documents: int = 0
    openers: list[dict[str, Any]] = field(default_factory=list)
    marker_examples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    layers: Counter = field(default_factory=Counter)


# ── text helpers ───────────────────────────────────────────────────────────


def _tokens(text_value: str) -> list[str]:
    return tokenize_words(text_value or "")


def _sentences(text_value: str) -> list[str]:
    """Split prose into sentences; never returns empty strings."""
    text_value = _SPACE_RE.sub(" ", (text_value or "").strip())
    if not text_value:
        return []
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text_value)]
    return [part for part in parts if part]


#: Site chrome that lives inside article bodies ("More: <related link>",
#: "Read more") — present in 2,332 news rows and never Zolai prose.
_CHROME_RE = re.compile(r"^(more\s*:|read more\b)", re.IGNORECASE)


def _is_site_chrome(block: str) -> bool:
    return _CHROME_RE.match(block.strip()) is not None


def _html_paragraphs(html: str) -> list[str]:
    """Visible prose paragraphs from article HTML, in document order."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        blocks = [
            _SPACE_RE.sub(" ", el.get_text(" ", strip=True)).strip()
            for el in soup.find_all(["p", "li", "blockquote", "h2", "h3"])
        ]
        blocks = [
            block
            for block in blocks
            if len(_tokens(block)) >= 3 and not _is_site_chrome(block)
        ]
        if blocks:
            return blocks
        fallback = soup.get_text("\n\n", strip=True)
    except Exception:  # bs4 missing or malformed markup — regex fallback
        fallback = _HTML_TAG_RE.sub("\n\n", html or "")
        fallback = unescape(fallback)
    return [
        block.strip()
        for block in re.split(r"\n\s*\n", fallback)
        if block.strip() and not _is_site_chrome(block)
    ]


def _markdown_paragraphs(markdown: str) -> list[str]:
    """Prose blocks from markdown; fenced code is dropped, link syntax unwrapped."""
    kept: list[str] = []
    in_fence = False
    for block in re.split(r"\n\s*\n", markdown or ""):
        stripped = block.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("|"):
            # Headings and tables are structure, not prose.
            stripped = _MARKDOWN_LINK_RE.sub(r"\1", stripped.lstrip("# ").strip())
        stripped = _MARKDOWN_LINK_RE.sub(r"\1", stripped)
        if len(_tokens(stripped)) >= 3:
            kept.append(_SPACE_RE.sub(" ", stripped).strip())
    return kept


def _slug(marker: str) -> str:
    slug = _SLUG_RE.sub("_", marker.lower()).strip("_")
    return slug or "x"


# ── reading sources ────────────────────────────────────────────────────────


def _iter_bible(engine: Engine, spec: SourceSpec, limit: int | None) -> Iterator[DocUnit]:
    sql = (
        f"SELECT {spec.id_col}, {spec.book_col}, {spec.chapter_col}, "
        f"{spec.verse_col}, {spec.ref_col}, {spec.text_col}, {spec.alt_col} "
        f"FROM {spec.table} WHERE {spec.where} ORDER BY {spec.id_col}"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    # Verses arrive in import order, and a chapter can be split across runs
    # (1CH 1:1–53 early, 1CH 1:54 at the tail).  Group by key instead of
    # assuming contiguity — otherwise the split half reuses the same unit ids
    # and the upsert silently collapses rows.
    groups: dict[tuple[str, int], list[Line]] = {}
    order: list[tuple[str, int]] = []
    with engine.connect() as conn:
        for row in conn.execute(text(sql)):
            key = (str(row[1] or ""), int(row[2] or 0))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(
                Line(
                    ref=str(row[4] or f"{key[0]} {key[1]}:{row[3]}"),
                    text=str(row[5] or ""),
                    alt=str(row[6] or ""),
                )
            )
    for key in order:
        yield DocUnit(
            source=spec.name,
            doc_id=key[0],
            title=key[0],
            group_id=str(key[1]),
            lines=groups[key],
        )


def _iter_documents(
    engine: Engine, spec: SourceSpec, max_docs: int | None
) -> Iterator[DocUnit]:
    link_col = f", {spec.link_col}" if spec.link_col else ""
    sql = (
        f"SELECT {spec.id_col}, {spec.title_col}, {spec.text_col}{link_col} "
        f"FROM {spec.table} WHERE {spec.where} ORDER BY {spec.id_col}"
    )
    if max_docs:
        sql += f" LIMIT {int(max_docs)}"
    with engine.connect() as conn:
        for row in conn.execute(text(sql)):
            doc_id = str(row[0])
            title = str(row[1] or doc_id)
            body = str(row[2] or "")
            link = str(row[3]) if link_col and row[3] else ""
            ref_base = link or doc_id
            if spec.format == "html":
                blocks = _html_paragraphs(body)
            elif spec.format == "markdown":
                blocks = _markdown_paragraphs(body)
            else:
                blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]

            paragraphs: list[list[Line]] = []
            for p_no, block in enumerate(blocks, start=1):
                sentences = _sentences(block)
                if not sentences:
                    continue
                paragraphs.append(
                    [
                        Line(ref=f"{ref_base}#p{p_no}.{s_no}", text=sentence)
                        for s_no, sentence in enumerate(sentences, start=1)
                    ]
                )
            if not paragraphs:
                continue
            all_lines = [line for paragraph in paragraphs for line in paragraph]
            yield DocUnit(
                source=spec.name,
                doc_id=doc_id,
                title=title,
                group_id="",
                lines=all_lines,
                explicit_paragraphs=paragraphs,
            )


def _has_table(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).first()
            is not None
        )


def iter_documents(
    engine: Engine,
    source: str,
    limit: int | None = None,
    max_docs: int | None = None,
) -> Iterator[DocUnit]:
    """Yield documents for ``source`` from the database.

    A source whose table is absent (fixture DB, partial ingest) yields
    nothing instead of failing the whole run.
    """
    spec = SOURCE_SPECS[source]
    if not _has_table(engine, spec.table):
        return
    if spec.kind == "verses":
        yield from _iter_bible(engine, spec, limit)
    else:
        yield from _iter_documents(engine, spec, max_docs)


# ── statistics ─────────────────────────────────────────────────────────────


def _document_frequency(
    documents: Sequence[DocUnit],
) -> tuple[Counter, int]:
    df: Counter = Counter()
    n_lines = 0
    for document in documents:
        for line in document.lines:
            tokens = set(_tokens(line.text))
            if not tokens:
                continue
            n_lines += 1
            df.update(tokens)
    return df, n_lines


def _idf(df: Counter, n_lines: int) -> dict[str, float]:
    n = max(1, n_lines)
    return {term: math.log((1.0 + n) / (1.0 + count)) + 1.0 for term, count in df.items()}


def _vector(tokens: Sequence[str], idf: dict[str, float]) -> dict[str, float]:
    weights: dict[str, float] = defaultdict(float)
    for token in tokens:
        weights[token] += idf.get(token, 1.0)
    norm = math.sqrt(sum(value * value for value in weights.values()))
    if norm == 0.0:
        return {}
    return {token: value / norm for token, value in weights.items()}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(token, 0.0) for token, value in left.items())


def _top_terms(tokens: Sequence[str], idf: dict[str, float], limit: int) -> list[list[Any]]:
    counts = Counter(tokens)
    scored = [
        (token, round(count * idf.get(token, 1.0), 3))
        for token, count in counts.items()
        if len(token) > 1
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [[token, score] for token, score in scored[:limit]]


def _top_terms_from_counter(
    counts: Counter, idf: dict[str, float], limit: int
) -> list[list[Any]]:
    scored = [
        (token, round(count * idf.get(token, 1.0), 3))
        for token, count in counts.items()
        if len(token) > 1
    ]
    scored.sort(key=lambda item: (-item[1], item[0]))
    return [[token, score] for token, score in scored[:limit]]


def segment_lines(lines: Sequence[Line], idf: dict[str, float]) -> list[tuple[int, int]]:
    """TextTiling over one group; returns inclusive ``(start, end)`` ranges.

    Boundaries are adjacent-line cosine dips: ``depth(i) = (sim(i-1) +
    sim(i+1)) / 2 - sim(i)``.  A dip becomes a cut when it clears both an
    absolute floor and the group's own ``mean + BOUNDARY_SIGMA·σ``, so dense
    narrative and repetitive genealogy both segment on their own scale.
    """
    n = len(lines)
    if n <= MIN_PARAGRAPH_LINES:
        return [(0, n - 1)] if n else []

    vectors = [_vector(_tokens(line.text), idf) for line in lines]
    sims = [_cosine(vectors[i], vectors[i + 1]) for i in range(n - 1)]

    depths: dict[int, float] = {}
    # gap g sits between line g-1 and line g; both neighbour similarities
    # (sim[g-2] and sim[g]) must exist, so g runs from 2 to n-2.
    for gap in range(2, n - 1):
        depths[gap] = (sims[gap - 2] + sims[gap]) / 2.0 - sims[gap - 1]

    if depths:
        values = list(depths.values())
        threshold = max(
            BOUNDARY_MIN_DEPTH,
            statistics.fmean(values) + BOUNDARY_SIGMA * statistics.pstdev(values),
        )
    else:
        threshold = BOUNDARY_MIN_DEPTH

    cuts = [gap for gap, depth in sorted(depths.items()) if depth >= threshold]
    ranges: list[tuple[int, int]] = []
    start = 0
    for gap in cuts:
        if gap - start >= MIN_PARAGRAPH_LINES:
            ranges.append((start, gap - 1))
            start = gap
    ranges.append((start, n - 1))
    return ranges


# ── feature extraction ─────────────────────────────────────────────────────


def _line_features(line: Line, idf: dict[str, float]) -> dict[str, Any]:
    tokens = _tokens(line.text)
    prefix = any(tok in {"ki", "ka", "na", "a", "i"} for tok in tokens)
    return {
        "tokens": len(tokens),
        "clause": classify_clause(line.text),
        "structure": extract_structure(line.text),
        "initial": tokens[0] if tokens else "",
        "final": tokens[-1] if tokens else "",
        "top_terms": _top_terms(tokens, idf, LINE_TERMS),
        "hyphenated": sum(1 for tok in tokens if "-" in tok),
        "agreement_prefix": prefix,
        "chars": len(line.text),
    }


def _paragraph_features(
    lines: Sequence[Line], idf: dict[str, float]
) -> dict[str, Any]:
    tokens = [tok for line in lines for tok in _tokens(line.text)]
    clauses = Counter(classify_clause(line.text) for line in lines)
    return {
        "lines": len(lines),
        "tokens": len(tokens),
        "unique_tokens": len(set(tokens)),
        "avg_line_tokens": round(len(tokens) / max(1, len(lines)), 2),
        "clause_types": dict(clauses.most_common()),
        "initial_token": tokens[0] if tokens else "",
        "final_token": tokens[-1] if tokens else "",
        "top_terms": _top_terms(tokens, idf, PARAGRAPH_TERMS),
    }


def _document_features(
    paragraphs: Sequence[Sequence[Line]], idf: dict[str, float]
) -> dict[str, Any]:
    lines = [line for paragraph in paragraphs for line in paragraph]
    tokens = [tok for line in lines for tok in _tokens(line.text)]
    clauses = Counter(classify_clause(line.text) for line in lines)
    return {
        "lines": len(lines),
        "paragraphs": len(paragraphs),
        "tokens": len(tokens),
        "unique_tokens": len(set(tokens)),
        "avg_line_tokens": round(len(tokens) / max(1, len(lines)), 2),
        "avg_lines_per_paragraph": round(len(lines) / max(1, len(paragraphs)), 2),
        "clause_types": dict(clauses.most_common()),
        "top_terms": _top_terms(tokens, idf, DOCUMENT_TERMS),
    }


# ── openers ────────────────────────────────────────────────────────────────


def learn_openers(
    paragraph_starts: Sequence[tuple[str, str]],
    corpus: OpenerCorpus,
) -> list[dict[str, Any]]:
    """Learn paragraph openers over-represented at paragraph starts.

    ``paragraph_starts`` is ``(unigram, bigram)`` per paragraph.  A marker's
    paragraph-start rate is compared with its rate across the whole corpus, so
    a discourse phrase used at boundaries lifts while a phrase that is simply
    frequent everywhere does not.  Requiring ``OPENER_MIN_LIFT`` rules out
    markers that are only as common at starts as anywhere else.
    """
    n_paragraphs = max(1, len(paragraph_starts))
    required = max(OPENER_MIN_COUNT, math.ceil(OPENER_MIN_SHARE * n_paragraphs))
    para_uni: Counter = Counter(uni for uni, _ in paragraph_starts if uni)
    para_bi: Counter = Counter(bi for _, bi in paragraph_starts if bi)

    found: list[dict[str, Any]] = []
    for kind, counts, base_counts, slots in (
        ("unigram", para_uni, corpus.unigrams, max(1, corpus.unigram_slots)),
        ("bigram", para_bi, corpus.bigrams, max(1, corpus.bigram_slots)),
    ):
        for marker, count in counts.items():
            if count < required:
                continue
            p_para = count / n_paragraphs
            p_corpus = base_counts.get(marker, 0) / slots
            if p_corpus <= 0.0:
                continue
            lift = p_para / p_corpus
            if lift < OPENER_MIN_LIFT:
                continue
            found.append(
                {
                    "marker": marker,
                    "kind": kind,
                    "count": count,
                    "lift": round(lift, 3),
                    "confidence": round(min(1.0, lift / OPENER_LIFT_FULL), 4),
                }
            )
    found.sort(key=lambda item: (-item["count"], -item["lift"], item["marker"]))
    return found


# ── layer construction ─────────────────────────────────────────────────────


def _build_source_layers(
    documents: Sequence[DocUnit],
    idf: dict[str, float],
    levels: Sequence[str],
    sample: dict[str, Any],
) -> tuple[list[LayerRow], SourceResult, list[dict[str, Any]]]:
    rows: list[LayerRow] = []
    result = SourceResult()
    opener_input: list[dict[str, Any]] = []
    paragraph_starts: list[tuple[str, str]] = []
    corpus = OpenerCorpus()
    corpus_tokens: Counter = Counter()
    corpus_clauses: Counter = Counter()
    # A Bible book arrives as one DocUnit per chapter; document-level features
    # must aggregate every chapter under the same doc_id.
    doc_paragraphs: dict[str, list[list[Line]]] = defaultdict(list)
    doc_titles: dict[str, str] = {}
    doc_groups: Counter = Counter()

    for document in documents:
        if not document.lines:
            result.skipped_documents += 1
            continue

        paragraphs: list[list[Line]]
        if document.explicit_paragraphs is not None:
            paragraphs = []
            for block in document.explicit_paragraphs:
                if len(block) > MAX_PARAGRAPH_LINES:
                    for start, end in segment_lines(block, idf):
                        paragraphs.append(block[start : end + 1])
                else:
                    paragraphs.append(list(block))
        else:
            paragraphs = [
                document.lines[start : end + 1]
                for start, end in segment_lines(document.lines, idf)
            ]

        flat = [line for paragraph in paragraphs for line in paragraph]
        doc_tokens = [tok for line in flat for tok in _tokens(line.text)]
        if len(doc_tokens) < MIN_DOCUMENT_TOKENS:
            result.skipped_documents += 1
            continue

        result.documents += 1
        result.lines += len(flat)
        result.paragraphs += len(paragraphs)
        result.tokens += len(doc_tokens)
        corpus_tokens.update(doc_tokens)
        corpus_clauses.update(classify_clause(line.text) for line in flat)
        doc_paragraphs[document.doc_id].extend(paragraphs)
        doc_titles[document.doc_id] = document.title
        doc_groups[document.doc_id] += 1

        corpus.add_tokens(doc_tokens)

        for p_no, paragraph in enumerate(paragraphs, start=1):
            p_tokens = [tok for line in paragraph for tok in _tokens(line.text)]
            p_id = f"{document.doc_id}|{document.group_id or 'doc'}p{p_no}"
            if "paragraph" in levels:
                rows.append(
                    LayerRow(
                        source=document.source,
                        level="paragraph",
                        unit_id=p_id,
                        doc_id=document.doc_id,
                        group_id=document.group_id,
                        ref=paragraph[0].ref,
                        features=_paragraph_features(paragraph, idf),
                        confidence=1.0,
                    )
                )
            result.layers["paragraph"] += 1
            if len(paragraphs) < 2:
                # A one-paragraph document cannot show that a phrase opens
                # paragraphs rather than just sentences.
                continue
            uni = p_tokens[0] if p_tokens else ""
            bi = " ".join(p_tokens[:2]) if len(p_tokens) >= 2 else ""
            paragraph_starts.append((uni, bi))
            paragraph_text = _SPACE_RE.sub(" ", " ".join(line.text for line in paragraph))[:240]
            for marker in (uni, bi):
                if marker and len(result.marker_examples.setdefault(marker, [])) < 3:
                    result.marker_examples[marker].append(
                        {"ref": paragraph[0].ref, "text": paragraph_text}
                    )
            if len(opener_input) < SAMPLE_LIMIT:
                opener_input.append(
                    {
                        "marker": bi or uni,
                        "ref": paragraph[0].ref,
                        "text": paragraph_text,
                    }
                )

        if "line" in levels:
            for index, line in enumerate(flat):
                rows.append(
                    LayerRow(
                        source=document.source,
                        level="line",
                        unit_id=f"{document.doc_id}|{document.group_id or 'doc'}|{index}",
                        doc_id=document.doc_id,
                        group_id=document.group_id,
                        ref=line.ref,
                        features=_line_features(line, idf),
                        confidence=1.0,
                    )
                )
        result.layers["line"] += len(flat)

    if "document" in levels:
        for doc_id, paragraphs in doc_paragraphs.items():
            features = _document_features(paragraphs, idf)
            if doc_groups[doc_id] > 1:
                features["chapters"] = doc_groups[doc_id]
            rows.append(
                LayerRow(
                    source=documents[0].source,
                    level="document",
                    unit_id=doc_id,
                    doc_id=doc_id,
                    group_id="",
                    ref=doc_titles.get(doc_id, doc_id),
                    features=features,
                    confidence=1.0,
                )
            )
            result.layers["document"] += 1

    openers = learn_openers(paragraph_starts, corpus)
    result.openers = openers
    result.documents = len(doc_paragraphs)

    if "corpus" in levels:
        rows.append(
            LayerRow(
                source=documents[0].source if documents else "",
                level="corpus",
                unit_id="ALL",
                doc_id="",
                group_id="",
                ref=SOURCE_SPECS[documents[0].source].label if documents else "",
                features={
                    "documents": result.documents,
                    "lines": result.lines,
                    "paragraphs": result.paragraphs,
                    "tokens": result.tokens,
                    "unique_tokens": len(corpus_tokens),
                    "clause_types": dict(corpus_clauses.most_common()),
                    "top_terms": _top_terms_from_counter(
                        corpus_tokens, idf, CORPUS_TERMS
                    ),
                    "openers": [
                        {"marker": item["marker"], "count": item["count"], "lift": item["lift"]}
                        for item in openers[:10]
                    ],
                },
                confidence=1.0,
            )
        )
        result.layers["corpus"] += 1

    sample[documents[0].source if documents else "?"] = {
        "documents": result.documents,
        "lines": result.lines,
        "paragraphs": result.paragraphs,
        "skipped": result.skipped_documents,
        "openers": openers[:5],
        "paragraph_samples": opener_input[:3],
    }
    return rows, result, openers


# ── writers ────────────────────────────────────────────────────────────────


def ensure_tables(engine: Engine) -> dict[str, list[str]]:
    """Create ``corpus_learning_layers`` (+ indexes) if missing — additive only."""
    from zolai.data.migrations import (
        CORPUS_LEARNING_LAYER_INDEXES,
        CORPUS_LEARNING_LAYERS_DDL,
    )

    created: list[str] = []
    skipped: list[str] = []
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='corpus_learning_layers'"
            )
        ).first()
        conn.execute(text(CORPUS_LEARNING_LAYERS_DDL))
        (skipped if exists else created).append("corpus_learning_layers")
        for ddl in CORPUS_LEARNING_LAYER_INDEXES:
            conn.execute(text(ddl))
        # A throwaway ``init_db()`` schema predates the ``book`` sentinel column
        # (the live corpus got it from a migration); opener cleanup needs it.
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(grammar_patterns)"))
        }
        if "book" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE grammar_patterns ADD COLUMN book TEXT NOT NULL DEFAULT ''"
                )
            )
            created.append("grammar_patterns.book")
    return {"created": created, "skipped": skipped}


_UPSERT_LAYER = text(
    "INSERT INTO corpus_learning_layers "
    "(source, level, unit_id, doc_id, group_id, ref, features, confidence, "
    " source_hash, updated_at) "
    "VALUES (:source, :level, :unit_id, :doc_id, :group_id, :ref, :features, "
    " :confidence, :source_hash, :updated_at) "
    "ON CONFLICT(source, level, unit_id) DO UPDATE SET "
    " doc_id = excluded.doc_id, group_id = excluded.group_id, "
    " ref = excluded.ref, features = excluded.features, "
    " confidence = excluded.confidence, source_hash = excluded.source_hash, "
    " updated_at = excluded.updated_at"
)


def _write_layers(
    engine: Engine, rows: Sequence[LayerRow], sources: Sequence[str]
) -> dict[str, int]:
    """Replace the layer rows of the given sources in one transaction."""
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM corpus_learning_layers WHERE source IN :sources"
            ).bindparams(bindparam("sources", expanding=True)),
            {"sources": list(sources)},
        )
        for start in range(0, len(rows), 500):
            batch = rows[start : start + 500]
            conn.execute(
                _UPSERT_LAYER,
                [
                    {
                        "source": row.source,
                        "level": row.level,
                        "unit_id": row.unit_id,
                        "doc_id": row.doc_id,
                        "group_id": row.group_id,
                        "ref": row.ref,
                        "features": row.features_json,
                        "confidence": row.confidence,
                        "source_hash": row.source_hash,
                        "updated_at": now,
                    }
                    for row in batch
                ],
            )
            written += len(batch)
    return {"written": written}


def _opener_pattern_id(source: str, marker: str) -> str:
    return f"disc_p_{source}_{_slug(marker)}"


def _write_openers(
    engine: Engine,
    source: str,
    openers: Sequence[dict[str, Any]],
    samples: Sequence[dict[str, Any]],
    marker_examples: dict[str, list[dict[str, Any]]],
    dry_run: bool,
) -> dict[str, int]:
    """Upsert learned openers into grammar_patterns + foundation_evidence."""
    spec = SOURCE_SPECS[source]
    tier_source, _, _ = EVIDENCE_TIER_MAP[spec.tier]
    pattern_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []

    # The id is a slug, so distinct markers can normalize to the same row
    # (``tua-ciangin`` vs ``tua ciangin``). Keep the stronger one and report
    # the collision instead of letting the later row silently overwrite it.
    unique: dict[str, dict[str, Any]] = {}
    collisions = 0
    for item in openers:
        pattern_id = _opener_pattern_id(source, item["marker"])
        prior = unique.get(pattern_id)
        if prior is None:
            unique[pattern_id] = item
            continue
        collisions += 1
        if (item["count"], item["lift"]) > (prior["count"], prior["lift"]):
            unique[pattern_id] = item

    for pattern_id, item in unique.items():
        pool = marker_examples.get(item["marker"]) or samples
        example = pool[0] if pool else {}
        examples = [
            {"zolai": sample["text"], "ref": sample.get("ref", ""), "source": source}
            for sample in pool[:3]
            if sample.get("text")
        ]
        pattern_rows.append(
            {
                "pattern_id": pattern_id,
                "pattern": item["marker"],
                "description": (
                    f"Learned paragraph opener from {spec.label}: "
                    f"{item['count']} paragraphs, lift {item['lift']}x"
                ),
                "function": "paragraph_opener",
                "examples": json.dumps(examples, ensure_ascii=False),
                "frequency": int(item["count"]),
                "normalized": f"paragraph_opener:{item['marker']}",
                "components": json.dumps(
                    {"kind": item["kind"], "lift": item["lift"]}, sort_keys=True
                ),
                "sources": json.dumps([source]),
                "confidence": item["confidence"],
                "book": "PARAGRAPH_OPENER",
            }
        )
        evidence_rows.append(
            {
                "fact_type": "grammar",
                "fact_key": f"pattern:{pattern_id}",
                "tier": spec.tier,
                "source": tier_source,
                "method": "paragraph_discourse_learning",
                "extractor": "CorpusHierarchyLearner",
                "payload": {
                    "marker": item["marker"],
                    "kind": item["kind"],
                    "count": item["count"],
                    "lift": item["lift"],
                    "source": source,
                    "example_ref": example.get("ref", ""),
                    "example_text": example.get("text", "")[:160],
                },
            }
        )

    if dry_run:
        return {
            "patterns_written": 0,
            "evidence_rows": len(evidence_rows),
            "collisions": collisions,
            "dry_run": 1,
        }

    evidence_ids = upsert_evidence_bulk(engine, evidence_rows) if evidence_rows else []

    written = 0
    updated = 0
    with engine.begin() as conn:
        for index, row in enumerate(pattern_rows):
            evidence_id = evidence_ids[index] if index < len(evidence_ids) else None
            evidence_json = json.dumps(
                [evidence_id] if evidence_id is not None else [], ensure_ascii=False
            )
            existing = conn.execute(
                text("SELECT id FROM grammar_patterns WHERE pattern_id = :pid"),
                {"pid": row["pattern_id"]},
            ).first()
            if existing:
                conn.execute(
                    text(
                        "UPDATE grammar_patterns SET pattern = :pattern, "
                        "description = :description, function = :function, "
                        "examples = :examples, frequency = :frequency, "
                        "normalized = :normalized, components = :components, "
                        "sources = :sources, confidence = :confidence, "
                        "evidence_ids = :evidence, book = :book, "
                        "status = 'OBSERVED' "
                        "WHERE id = :id"
                    ),
                    {
                        **{key: row[key] for key in (
                            "pattern", "description", "function", "examples",
                            "frequency", "normalized", "components", "sources",
                            "confidence", "book",
                        )},
                        "evidence": evidence_json,
                        "id": existing[0],
                    },
                )
                updated += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO grammar_patterns (pattern_id, pattern, "
                        "description, function, examples, frequency, normalized, "
                        "components, sources, evidence_ids, confidence, book, "
                        "status) VALUES (:pattern_id, :pattern, :description, "
                        ":function, :examples, :frequency, :normalized, "
                        ":components, :sources, :evidence, :confidence, :book, "
                        "'OBSERVED')"
                    ),
                    {**row, "evidence": evidence_json},
                )
                written += 1

        keep = [row["pattern_id"] for row in pattern_rows]
        prefix = f"disc_p_{source}_%"
        if keep:
            conn.execute(
                text(
                    "DELETE FROM grammar_patterns WHERE book = 'PARAGRAPH_OPENER' "
                    "AND pattern_id LIKE :prefix AND pattern_id NOT IN :keep"
                ).bindparams(bindparam("keep", expanding=True)),
                {"prefix": prefix, "keep": keep},
            )
            conn.execute(
                text(
                    "DELETE FROM foundation_evidence WHERE fact_type = 'grammar' "
                    "AND method = 'paragraph_discourse_learning' "
                    "AND fact_key LIKE :prefix AND fact_key NOT IN :keep"
                ).bindparams(bindparam("keep", expanding=True)),
                {
                    "prefix": "grammar:pattern:" + prefix,
                    "keep": [f"grammar:pattern:{pid}" for pid in keep],
                },
            )
        else:
            conn.execute(
                text(
                    "DELETE FROM grammar_patterns WHERE book = 'PARAGRAPH_OPENER' "
                    "AND pattern_id LIKE :prefix"
                ),
                {"prefix": prefix},
            )
            conn.execute(
                text(
                    "DELETE FROM foundation_evidence WHERE fact_type = 'grammar' "
                    "AND method = 'paragraph_discourse_learning' "
                    "AND fact_key LIKE :prefix"
                ),
                {"prefix": "grammar:pattern:" + prefix},
            )
    return {
        "patterns_written": written,
        "patterns_updated": updated,
        "evidence_rows": len(evidence_ids),
        "collisions": collisions,
        "dry_run": 0,
    }


# ── main entry point ───────────────────────────────────────────────────────


def learn_layers(
    engine: Engine,
    *,
    dry_run: bool = False,
    sources: Sequence[str] | None = None,
    levels: Sequence[str] = DEFAULT_LEVELS,
    limit: int | None = None,
    max_docs: int | None = None,
) -> dict[str, Any]:
    """Study every source at every level; write layers, openers, evidence.

    Args:
        engine: Database to read and (unless ``dry_run``) write.
        dry_run: Compute and report only — no table is created or written.
        sources: Subset of :data:`SOURCE_SPECS` (default: all four).
        levels: Subset of ``('line', 'paragraph', 'document', 'corpus')``.
        limit: Maximum Bible verses read (source ``bible`` only).
        max_docs: Maximum documents read per document source.

    Returns:
        JSON-safe summary with per-source counters and the learned openers.
    """
    active_sources = list(sources or SOURCE_SPECS)
    levels = list(levels)
    unknown = [name for name in active_sources if name not in SOURCE_SPECS]
    if unknown:
        raise ValueError(f"unknown source(s): {', '.join(unknown)}")

    sample: dict[str, Any] = {}
    per_source: dict[str, Any] = {}
    total_layers = 0
    total_openers = 0

    if not dry_run:
        ensure_tables(engine)

    for name in active_sources:
        documents = list(iter_documents(engine, name, limit=limit, max_docs=max_docs))
        if not documents:
            per_source[name] = {"documents": 0, "note": "no rows"}
            continue
        df, n_lines = _document_frequency(documents)
        idf = _idf(df, n_lines)
        rows, result, openers = _build_source_layers(documents, idf, levels, sample)
        layer_stats = (
            _write_layers(engine, rows, [name]) if not dry_run else {"written": 0}
        )
        opener_stats = _write_openers(
            engine,
            name,
            openers,
            sample.get(name, {}).get("paragraph_samples", []),
            result.marker_examples,
            dry_run,
        )
        total_layers += len(rows)
        total_openers += len(openers)
        per_source[name] = {
            "documents": result.documents,
            "lines": result.lines,
            "paragraphs": result.paragraphs,
            "tokens": result.tokens,
            "skipped_documents": result.skipped_documents,
            "layers": len(rows),
            "layers_written": layer_stats.get("written", 0),
            "layers_removed": layer_stats.get("removed", 0),
            "openers": len(openers),
            **opener_stats,
        }

    return {
        "dry_run": dry_run,
        "levels": levels,
        "sources": active_sources,
        "layers_total": total_layers,
        "openers_total": total_openers,
        "per_source": per_source,
        "sample": sample,
    }
