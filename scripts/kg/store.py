"""Canonical-DB persistence shared by the KG extraction scripts.

There is exactly one database (``config.paths.db``) and exactly one graph
schema — ``kg_nodes`` / ``kg_edges`` from :mod:`zolai.data.migrations`.  The
extraction scripts used to open ``data/kg.db`` and write ``kg_node`` /
``kg_edge`` rows into a schema that was never created anywhere, so every run
either failed on the missing table or silently created a second store beside
the real one.  This module is the one place that knows how to write a node or
an edge.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from zolai.config import config
from zolai.shared.clock import SQL_NOW

#: ``kg_nodes.source`` / ``kg_edges.source`` value for rows written here, and
#: the namespace the ``source_id`` (a stable IRI) lives in.
SOURCE = "kg-extract"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the canonical DB (or an explicit path, for tests).

    ``busy_timeout`` rather than an exclusive lock: the learning cycle and the
    RAG build write to this same file and each waits up to 60 s for the lock.
    """
    conn = sqlite3.connect(str(db_path or config.paths.db), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def upsert_node(
    conn: sqlite3.Connection,
    *,
    label: str,
    node_type: str = "entity",
    source_id: str | None = None,
    confidence: float = 0.0,
    properties: dict | None = None,
) -> int:
    """Insert or refresh one node; returns its row id.

    Identity is ``(source, source_id)`` — the IRI — not the label, so renaming
    a label updates the existing node instead of forking the graph.
    """
    source_id = source_id or f"zolai:ent:{label.lower().replace(' ', '_')}"
    props = json.dumps(properties or {}, ensure_ascii=False)
    row = conn.execute(
        "SELECT id FROM kg_nodes WHERE source = ? AND source_id = ?", (SOURCE, source_id)
    ).fetchone()
    if row is not None:
        conn.execute(
            f"UPDATE kg_nodes SET label = ?, node_type = ?, properties = ?, "
            f"confidence = ?, updated_at = {SQL_NOW} WHERE id = ?",
            (label, node_type, props, confidence, row[0]),
        )
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO kg_nodes (node_type, label, properties, source, source_id, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (node_type, label, props, SOURCE, source_id, confidence),
    )
    return int(cur.lastrowid)


def upsert_edge(
    conn: sqlite3.Connection,
    *,
    source_row: int,
    target_row: int,
    relation: str,
    confidence: float = 0.0,
    properties: dict | None = None,
) -> int:
    """Insert one edge unless the same (source, target, relation) already exists."""
    row = conn.execute(
        "SELECT id FROM kg_edges WHERE source_id = ? AND target_id = ? AND relation = ? AND source = ?",
        (source_row, target_row, relation, SOURCE),
    ).fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO kg_edges (source_id, target_id, relation, properties, confidence, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_row, target_row, relation, json.dumps(properties or {}, ensure_ascii=False), confidence, SOURCE),
    )
    return int(cur.lastrowid)
