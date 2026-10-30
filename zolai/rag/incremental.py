"""Incremental RAG vector updates for the local learning cycle.

``build_knowledge_vectors(force=False)`` can only *skip* ids that already
exist: it cannot re-embed a row whose text changed, and it cannot remove a
vector whose source row was deleted. This module does both, bounded.

Two rules keep it safe against the live 269k-vector index:

* **Group by table, not by spec.** ``bible_verses`` has two specs
  (``zo_tdb77``, ``zo_tedim2010``); deleting "the vectors of source_id X" for
  one spec would silently drop the other version of the same verse.
* **Embed before deleting.** Every new piece is encoded first; only then does
  one short transaction delete the row's old vectors and insert the new ones.
  An embed failure leaves the old vectors serving traffic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from zolai.rag.build import (
    MAX_CHUNKS_PER_DOC,
    SOURCES,
    SourceSpec,
    _build_text_for_source,
    _chunk_text,
    _get_embedder,
)
from zolai.rag.vectors import encode_embedding
from zolai.shared.clock import stamp

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 200
DEFAULT_MAX_CHUNKS = 512
#: Rows/second used only for a dry-run estimate (measured on the live corpus).
ROWS_PER_SECOND = 11.0

_TS_COLUMNS = ("updated_at", "imported_at", "created_at")


def _group_sources(sources: Iterable[str] | None) -> list[tuple[str, list[SourceSpec]]]:
    """Specs grouped by table, preserving ``SOURCES`` order."""
    allowed = set(sources) if sources else None
    grouped: dict[str, list[SourceSpec]] = {}
    for spec in SOURCES:
        if allowed and spec.table not in allowed and spec.source_type not in allowed:
            continue
        grouped.setdefault(spec.table, []).append(spec)
    return list(grouped.items())


def _table_columns(conn, table: str) -> set[str] | None:
    exists = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).first()
    if not exists:
        return None
    return {
        str(row[1])
        for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }


def _usable_specs(specs: list[SourceSpec], columns: set[str]) -> list[SourceSpec]:
    """Drop specs whose declared columns this database does not have."""
    usable = []
    for spec in specs:
        needed = [spec.id_col, spec.text_col]
        if spec.definition_col:
            needed.append(spec.definition_col)
        needed.extend(spec.extra_cols)
        if all(col in columns for col in needed):
            usable.append(spec)
        else:
            log.warning("Skipping %s: missing columns %s", spec.label, needed)
    return usable


def _pick_ts_column(columns: set[str]) -> str | None:
    for name in _TS_COLUMNS:
        if name in columns:
            return name
    return None


def watermark(engine: Engine, table: str) -> str | None:
    """ISO stamp of the newest vector for ``table`` (``None`` when unbuilt)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT imported_at FROM knowledge_vectors "
                "WHERE source = :t AND imported_at IS NOT NULL "
                "ORDER BY julianday(imported_at) DESC LIMIT 1"
            ),
            {"t": table},
        ).first()
    return str(row[0]) if row else None


def effective_since(engine: Engine, table: str, since: str | None) -> str | None:
    """``--since`` unless the table's own watermark is newer."""
    mark = watermark(engine, table)
    if since and mark:
        with engine.connect() as conn:
            newer = conn.execute(
                text(
                    "SELECT CASE WHEN julianday(:a) >= julianday(:b) "
                    "THEN :a ELSE :b END"
                ),
                {"a": since, "b": mark},
            ).scalar()
        return str(newer)
    return since or mark


def _vector_count(conn, table: str, source_id: str) -> int:
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM knowledge_vectors WHERE source = :t "
            "AND json_extract(metadata, '$.source_id') = :sid"
        ),
        {"t": table, "sid": source_id},
    ).scalar()
    return int(row or 0)


def _failed_source_ids(engine: Engine, table: str, limit: int) -> list[str]:
    """Source ids that failed in an earlier run — retried first."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_id FROM learning_vectors_state "
                    "WHERE source_table = :t AND status = 'failed' "
                    "ORDER BY id DESC LIMIT :n"
                ),
                {"t": table, "n": limit},
            ).fetchall()
    except Exception:  # noqa: BLE001 — ledger absent means nothing to retry
        return []
    return [str(r[0]) for r in rows]


def _select_rows(
    conn,
    table: str,
    specs: list[SourceSpec],
    *,
    ts_col: str | None,
    since: str | None,
    reconcile: bool,
    limit: int,
    ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    id_col = specs[0].id_col
    select_cols: list[str] = []
    for spec in specs:
        select_cols.extend([spec.id_col, spec.text_col])
        if spec.definition_col:
            select_cols.append(spec.definition_col)
        select_cols.extend(spec.extra_cols)
    select_clause = ", ".join(dict.fromkeys(select_cols))

    where: list[str] = []
    params: dict[str, Any] = {"t": table, "n": limit}
    if ids:
        placeholders = []
        for i, value in enumerate(ids):
            key = f"id{i}"
            placeholders.append(f":{key}")
            params[key] = value
        where.append(f"CAST({id_col} AS TEXT) IN ({', '.join(placeholders)})")
    else:
        clauses = []
        if ts_col and since:
            clauses.append(f"julianday({ts_col}) >= julianday(:since)")
            params["since"] = since
        if reconcile:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM knowledge_vectors kv "
                "WHERE kv.source = :t AND "
                f"json_extract(kv.metadata, '$.source_id') = CAST({id_col} AS TEXT))"
            )
        if clauses:
            where.append("(" + " OR ".join(clauses) + ")")
        else:
            return []  # no timestamp, no reconcile: nothing is detectable

    sql = f"SELECT {select_clause} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if ts_col:
        sql += f" ORDER BY julianday({ts_col}) DESC"
    sql += " LIMIT :n"
    return [dict(row._mapping) for row in conn.execute(text(sql), params).fetchall()]


def _piece_ids(spec: SourceSpec, source_id: str, pieces: list[str]) -> list[tuple[str, str]]:
    """``(vector id, chunk text)`` — identical derivation to ``build.py``."""
    out: list[tuple[str, str]] = []
    for chunk_idx, piece in enumerate(pieces):
        key = (
            f"{spec.table}:{source_id}:{chunk_idx}:{piece[:50]}"
            if spec.chunk
            else f"{spec.table}:{source_id}:{piece[:50]}"
        )
        out.append((hashlib.sha256(key.encode()).hexdigest()[:32], piece))
    return out


def _row_pieces(
    specs: list[SourceSpec], row: dict[str, Any]
) -> tuple[list[tuple[str, str, SourceSpec]], int]:
    """All ``(vector id, text, spec)`` for one row, plus the source-id."""
    pieces: list[tuple[str, str, SourceSpec]] = []
    source_id = ""
    for spec in specs:
        content = _build_text_for_source(spec, row)
        source_id = str(row.get(spec.id_col, ""))
        if not content:
            continue
        chunks = _chunk_text(content) if spec.chunk else [content]
        if len(chunks) > MAX_CHUNKS_PER_DOC:
            chunks = chunks[:MAX_CHUNKS_PER_DOC]
        for vec_id, piece in _piece_ids(spec, source_id, chunks):
            pieces.append((vec_id, piece, spec))
    return pieces, source_id


def _insert_vectors(conn, records: list[dict[str, Any]]) -> None:
    conn.execute(
        text(
            "INSERT OR REPLACE INTO knowledge_vectors "
            "(id, text, metadata, embedding, source_type, source, version, imported_at) "
            "VALUES (:id, :text, :meta, :emb, :stype, :src, 1, :now)"
        ),
        records,
    )


def _record_state(
    engine: Engine,
    run_id: str | None,
    table: str,
    source_id: str,
    *,
    action: str,
    status: str,
    vectors_before: int = 0,
    vectors_after: int = 0,
    chunks: int = 0,
    text_sha256: str | None = None,
    error_class: str | None = None,
) -> None:
    if not run_id:
        return
    from zolai.learning.run_ledger import record_vector_state

    record_vector_state(
        engine,
        run_id,
        source_table=table,
        source_id=source_id,
        action=action,
        status=status,
        vectors_before=vectors_before,
        vectors_after=vectors_after,
        chunks=chunks,
        text_sha256=text_sha256,
        error_class=error_class,
    )


def prune_stale_vectors(engine: Engine, table: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, int]:
    """Delete attributed vectors whose source row is gone.

    Legacy vectors with no ``source_id`` cannot be attributed to a row and are
    counted, never deleted.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT json_extract(metadata, '$.source_id') AS sid "
                "FROM knowledge_vectors WHERE source = :t"
            ),
            {"t": table},
        ).fetchall()
        columns = _table_columns(conn, table)
        id_col = "id"
        for spec in SOURCES:
            if spec.table == table:
                id_col = spec.id_col
                break
        live = set()
        if columns is not None:
            live = {
                str(r[0])
                for r in conn.execute(
                    text(f"SELECT CAST({id_col} AS TEXT) FROM {table}")
                ).fetchall()
            }

    attributable = [str(r[0]) for r in rows if r[0] is not None]
    unattributable = sum(1 for r in rows if r[0] is None)
    stale = [sid for sid in attributable if sid not in live][:limit]
    if stale:
        with engine.begin() as conn:
            for sid in stale:
                conn.execute(
                    text(
                        "DELETE FROM knowledge_vectors WHERE source = :t "
                        "AND json_extract(metadata, '$.source_id') = :sid"
                    ),
                    {"t": table, "sid": sid},
                )
    return {
        "pruned": len(stale),
        "unattributable": unattributable,
        "candidates": len(attributable),
    }


def update_knowledge_vectors_incremental(
    engine: Engine,
    *,
    limit: int = DEFAULT_LIMIT,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    sources: Iterable[str] | None = None,
    since: str | None = None,
    prune_stale: bool = False,
    reconcile: bool = False,
    dry_run: bool = False,
    run_id: str | None = None,
    embedder: Any = None,
) -> dict[str, Any]:
    """Re-embed changed rows, bounded by ``limit`` rows / ``max_chunks``."""
    started = time.time()
    summary: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "apply",
        "tables": {},
        "reembedded": 0,
        "failed": 0,
        "skipped": 0,
        "chunks": 0,
        "vectors_before": 0,
        "vectors_after": 0,
        "pruned": 0,
        "unattributable": 0,
        "errors": [],
    }

    groups = _group_sources(sources)
    if not groups:
        summary["errors"].append("no matching sources")
        return summary

    embedder_loaded = False
    chunk_budget = max_chunks
    rows_budget = limit
    plan: list[dict[str, Any]] = []
    to_prune: list[str] = []
    planned_chunks = 0

    with engine.connect() as conn:
        for table, specs in groups:
            columns = _table_columns(conn, table)
            entry: dict[str, Any] = {
                "status": "missing" if columns is None else "ok",
                "candidates": 0,
                "chunks": 0,
                "reembedded": 0,
                "failed": 0,
                "skipped": 0,
                "budget_skipped": 0,
                "vectors_before": 0,
                "vectors_after": 0,
            }
            summary["tables"][table] = entry
            if columns is None:
                continue
            usable = _usable_specs(specs, columns)
            if not usable:
                entry["status"] = "no usable specs"
                continue
            ts_col = _pick_ts_column(columns)
            effective = effective_since(engine, table, since)
            entry["ts_column"] = ts_col
            entry["since"] = effective
            if ts_col is None and not reconcile:
                entry["status"] = "no timestamp column (use reconcile)"
                continue

            retry_ids = _failed_source_ids(engine, table, limit)
            rows = _select_rows(
                conn,
                table,
                usable,
                ts_col=ts_col,
                since=effective,
                reconcile=reconcile,
                limit=limit,
            )
            if retry_ids:
                have = {str(r.get(usable[0].id_col, "")) for r in rows}
                missing = [sid for sid in retry_ids if sid not in have]
                if missing:
                    rows = _select_rows(
                        conn,
                        table,
                        usable,
                        ts_col=ts_col,
                        since=effective,
                        reconcile=reconcile,
                        limit=len(missing),
                        ids=missing,
                    ) + rows

            for row in rows:
                pieces, source_id = _row_pieces(usable, row)
                if not pieces:
                    entry["skipped"] += 1
                    summary["skipped"] += 1
                    continue
                entry["candidates"] += 1
                entry["chunks"] += len(pieces)
                # A row is embedded only when its *whole* chunk set fits the
                # remaining budget — a half-written document is worse than a
                # deferred one.
                if (
                    len(plan) >= limit
                    or planned_chunks + len(pieces) > chunk_budget
                ):
                    entry["budget_skipped"] += 1
                    summary["skipped"] += 1
                    continue
                planned_chunks += len(pieces)
                plan.append(
                    {
                        "table": table,
                        "specs": usable,
                        "row": row,
                        "source_id": source_id,
                        "pieces": pieces,
                    }
                )

            if prune_stale and not dry_run:
                to_prune.append(table)

    for table in to_prune:
        pruned = prune_stale_vectors(engine, table, limit=limit)
        summary["pruned"] += pruned["pruned"]
        summary["unattributable"] += pruned["unattributable"]
        summary["tables"][table]["pruned"] = pruned["pruned"]
        summary["tables"][table]["unattributable"] = pruned["unattributable"]

    summary["candidate_rows"] = len(plan)
    if dry_run:
        summary["planned_chunks"] = sum(len(item["pieces"]) for item in plan)
        summary["eta_seconds"] = round(len(plan) / ROWS_PER_SECOND, 1)
        summary["embedder"] = "not loaded (dry-run)"
        # ``vectors_before``/``after`` count only the rows a run touched, so
        # they stay 0 here. Report the real index size separately: a bare 0
        # beside ``candidates: 10`` reads as "the table is empty".
        with engine.connect() as conn:
            index_counts = dict(
                conn.execute(
                    text("SELECT source, COUNT(*) FROM knowledge_vectors GROUP BY source")
                ).all()
            )
        for table, entry in summary["tables"].items():
            entry["vectors_in_index"] = int(index_counts.get(table, 0))
        summary["vectors_in_index"] = sum(
            int(count) for count in index_counts.values()
        )
        summary["elapsed_seconds"] = round(time.time() - started, 2)
        return summary

    if embedder is None:
        embedder = _get_embedder()
        embedder_loaded = True
    if not embedder:
        summary["errors"].append("sentence-transformers not available")
        summary["embedder"] = "missing"
        summary["elapsed_seconds"] = round(time.time() - started, 2)
        return summary

    summary["embedder"] = "loaded" if embedder_loaded else "injected"

    for item in plan:
        table = item["table"]
        specs: list[SourceSpec] = item["specs"]
        row = item["row"]
        source_id = item["source_id"]
        pieces = item["pieces"]
        entry = summary["tables"][table]

        if len(pieces) > chunk_budget or rows_budget <= 0:
            entry["skipped"] += 1
            summary["skipped"] += 1
            _record_state(
                engine,
                run_id,
                table,
                source_id,
                action="skip",
                status="ok",
                chunks=len(pieces),
                error_class="budget",
            )
            continue

        with engine.connect() as conn:
            before = _vector_count(conn, table, source_id)

        texts = [piece for _, piece, _ in pieces]
        try:
            embeddings = embedder.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
        except Exception as exc:  # noqa: BLE001 — old vectors must survive
            log.warning("Embed failed for %s:%s: %s", table, source_id, exc)
            entry["failed"] += 1
            summary["failed"] += 1
            summary["errors"].append(f"{table}:{source_id}: {type(exc).__name__}")
            _record_state(
                engine,
                run_id,
                table,
                source_id,
                action="failed",
                status="failed",
                vectors_before=before,
                chunks=len(pieces),
                error_class=type(exc).__name__,
            )
            continue

        now = stamp()
        records = [
            {
                "id": vec_id,
                "text": piece,
                "meta": json.dumps(
                    {
                        "source_type": spec.source_type,
                        "source": table,
                        "source_table": table,
                        "source_id": source_id,
                    }
                ),
                "emb": encode_embedding(emb),
                "stype": spec.source_type,
                "src": table,
                "now": now,
            }
            for (vec_id, piece, spec), emb in zip(pieces, embeddings)
        ]

        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM knowledge_vectors WHERE source = :t "
                        "AND json_extract(metadata, '$.source_id') = :sid"
                    ),
                    {"t": table, "sid": source_id},
                )
                _insert_vectors(conn, records)
            with engine.connect() as conn:
                after = _vector_count(conn, table, source_id)
        except Exception as exc:  # noqa: BLE001 — transactional: nothing changed
            log.warning("Vector write failed for %s:%s: %s", table, source_id, exc)
            entry["failed"] += 1
            summary["failed"] += 1
            summary["errors"].append(f"{table}:{source_id}: {type(exc).__name__}")
            _record_state(
                engine,
                run_id,
                table,
                source_id,
                action="failed",
                status="failed",
                vectors_before=before,
                chunks=len(pieces),
                error_class=type(exc).__name__,
            )
            continue

        digest = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
        entry["reembedded"] += 1
        entry["vectors_before"] += before
        entry["vectors_after"] += after
        summary["reembedded"] += 1
        summary["chunks"] += len(pieces)
        summary["vectors_before"] += before
        summary["vectors_after"] += after
        chunk_budget -= len(pieces)
        rows_budget -= 1
        _record_state(
            engine,
            run_id,
            table,
            source_id,
            action="reembed",
            status="ok",
            vectors_before=before,
            vectors_after=after,
            chunks=len(pieces),
            text_sha256=digest,
        )

    summary["elapsed_seconds"] = round(time.time() - started, 2)
    return summary
