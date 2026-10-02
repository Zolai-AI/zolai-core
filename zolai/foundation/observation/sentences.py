"""Capability 7 — declarative sentence extraction from the canonical DB.

A :class:`SentenceSource` is one *Zolai* text column of one table plus the SQL
fragments that derive its document id and sentence id.  The list
:data:`SENTENCE_SOURCES` is the whole extraction surface — adding a corpus is a
data change here, not a code change in the pipeline.

Design rules (plan §19 / capability row 7):

- **Zolai only.**  The parallel table's English↔Myanmar direction is excluded
  by the source ``WHERE`` clause (150,965 rows), and only Zolai columns are
  selected everywhere else.
- **Pre-segmented only.**  Every source already stores one sentence (or one
  multi-word expression) per row; free-text splitting is deferred to Phase 5.
- **Stable refs.**  ``source_ref = {table}:{field}:{row_id}`` — unique across
  sources (two Zolai Bible columns share a row id, so the field is part of the
  key) and stable across rebuilds, which is what makes the observation insert
  idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

__all__ = [
    "SENTENCE_SOURCES",
    "Sentence",
    "SentenceSource",
    "available_sources",
    "iter_sentences",
    "select_sources",
]

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class SentenceSource:
    """One Zolai sentence column, with its document/sentence id derivations."""

    name: str
    """Stable source key (used by ``--sources`` and the summary)."""

    table: str
    field: str
    """Zolai column (or ``zolai`` for the direction-derived parallel side)."""

    select_sql: str
    """SQL expression yielding the Zolai sentence text."""

    document_sql: str
    """SQL expression yielding the document id (book / direction / 'phrases')."""

    sentence_sql: str
    """SQL expression yielding the sentence id (ref / reference / row id)."""

    where: str
    """Extra ``WHERE`` fragment — always Zolai-only."""

    method: str = "pre_segmented"

    @property
    def source_id(self) -> str:
        """``{table}:{field}`` — the source identity stored on each row."""
        return f"{self.table}:{self.field}"


#: The corpus surface of the observation engine (order = build order).
SENTENCE_SOURCES: tuple[SentenceSource, ...] = (
    SentenceSource(
        name="bible_tdb77",
        table="bible_verses",
        field="zo_tdb77",
        select_sql="zo_tdb77",
        document_sql="book",
        sentence_sql="ref",
        where="zo_tdb77 IS NOT NULL AND TRIM(zo_tdb77) <> ''",
    ),
    SentenceSource(
        name="bible_tedim2010",
        table="bible_verses",
        field="zo_tedim2010",
        select_sql="zo_tedim2010",
        document_sql="book",
        sentence_sql="ref",
        where="zo_tedim2010 IS NOT NULL AND TRIM(zo_tedim2010) <> ''",
    ),
    SentenceSource(
        name="translations_zo",
        table="translations",
        field="zolai",
        # Zolai side of each direction: source=Zolai for zo→en, target=Zolai
        # for en→zo.  The English→Myanmar direction carries no Zolai at all.
        select_sql="CASE WHEN direction = 'zo_to_en' THEN source ELSE target END",
        document_sql="direction",
        sentence_sql="reference",
        where="direction IN ('zo_to_en', 'en_to_zo')",
    ),
    SentenceSource(
        name="phrases",
        table="phrases",
        field="zolai",
        select_sql="zolai",
        document_sql="'phrases'",
        sentence_sql="CAST(id AS TEXT)",
        where="zolai IS NOT NULL AND TRIM(zolai) <> ''",
    ),
)


@dataclass(frozen=True)
class Sentence:
    """One extracted sentence, ready to be tokenized."""

    text: str
    source: SentenceSource
    row_id: int
    document_id: str
    sentence_id: str

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def source_ref(self) -> str:
        """Unique, stable reference — the observation idempotency key."""
        return f"{self.source.source_id}:{self.row_id}"


def _require_safe_ident(value: str) -> str:
    if not _SAFE_IDENT.match(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def select_sources(names: Sequence[str] | None) -> list[SentenceSource]:
    """Resolve ``names`` (or all) to sources, preserving declaration order.

    Raises:
        ValueError: for an unknown source name.
    """
    if not names:
        return list(SENTENCE_SOURCES)
    wanted = list(dict.fromkeys(names))
    by_name = {s.name: s for s in SENTENCE_SOURCES}
    unknown = [n for n in wanted if n not in by_name]
    if unknown:
        raise ValueError(
            f"unknown source(s) {unknown!r}; available: {sorted(by_name)}"
        )
    # Declaration order, not argument order — deterministic build order.
    return [s for s in SENTENCE_SOURCES if s.name in set(wanted)]


def available_sources(engine: Engine) -> list[SentenceSource]:
    """Sources whose table exists in ``engine``'s store (CI-seeded DBs)."""
    from sqlalchemy import inspect as sa_inspect

    tables = set(sa_inspect(engine).get_table_names())
    return [s for s in SENTENCE_SOURCES if s.table in tables]


def iter_sentences(
    conn: Connection,
    source: SentenceSource,
    *,
    limit: int | None = None,
    batch_size: int = 2000,
) -> Iterator[Sentence]:
    """Stream every Zolai sentence of ``source`` in ``id`` order (keyset).

    Args:
        conn: Open SQLAlchemy connection (a transaction is started by caller).
        source: The declarative source to read.
        limit: Stop after this many sentences (``None`` = all rows).
        batch_size: Keyset page size — memory stays flat for 31k+ row tables.

    Yields:
        :class:`Sentence` rows with non-empty, tokenizable text only.
    """
    _require_safe_ident(source.table)
    select = (
        f"SELECT id AS row_id, ({source.select_sql}) AS text, "
        f"({source.document_sql}) AS document_id, "
        f"({source.sentence_sql}) AS sentence_id "
        f"FROM {source.table} "
        f"WHERE ({source.where}) AND id > :last_id "
        f"ORDER BY id LIMIT :n"
    )
    last_id = 0
    emitted = 0
    page = batch_size if limit is None else min(batch_size, limit)
    while True:
        rows = conn.execute(
            text(select),
            {"last_id": last_id, "n": page},
        ).fetchall()
        if not rows:
            return
        for row in rows:
            row_text = row.text
            if not isinstance(row_text, str) or not row_text.strip():
                continue
            yield Sentence(
                text=row_text,
                source=source,
                row_id=int(row.row_id),
                document_id=str(row.document_id),
                sentence_id=str(row.sentence_id),
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return
        last_id = int(rows[-1].row_id)
        if len(rows) < page:
            return
