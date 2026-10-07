"""DB-first evaluation set storage for ZolaiBench — Pure Database."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class EvalSet:
    """Evaluation set metadata."""
    name: str
    task: str  # tokenization, pos, morph, grammar
    description: str
    version: str
    item_count: int
    created_at: str


@dataclass
class EvalItem:
    """Single evaluation item."""
    id: int
    eval_set: str
    input_text: str
    gold_annotation: str  # JSON string
    metadata: str  # JSON string (source, difficulty, etc.)


def get_eval_db_path() -> Path:
    """Get path to evaluation database."""
    db_path = "/home/peter/Documents/Projects/zolai-ai/data/zolai.db"
    return Path(db_path).parent / "zolai_eval.db"


@contextmanager
def get_eval_connection() -> Iterator[sqlite3.Connection]:
    """Get connection to evaluation database."""
    db_path = get_eval_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_eval_db() -> None:
    """Initialize evaluation database tables."""
    with get_eval_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS eval_sets (
                name TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                description TEXT,
                version TEXT NOT NULL DEFAULT 'v0',
                item_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS eval_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_set TEXT NOT NULL,
                input_text TEXT NOT NULL,
                gold_annotation TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY (eval_set) REFERENCES eval_sets(name)
            );

            CREATE INDEX IF NOT EXISTS ix_eval_items_set ON eval_items(eval_set);
            CREATE INDEX IF NOT EXISTS ix_eval_items_input ON eval_items(input_text);
        """)
        conn.commit()


def create_eval_set(
    name: str,
    task: str,
    description: str,
    version: str = "v0",
) -> EvalSet:
    """Create a new evaluation set."""
    init_eval_db()
    with get_eval_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO eval_sets (name, task, description, version, item_count) VALUES (?, ?, ?, ?, 0)",
            (name, task, description, version)
        )
        conn.commit()
    return EvalSet(name=name, task=task, description=description, version=version, item_count=0, created_at="")


def add_eval_item(
    eval_set: str,
    input_text: str,
    gold_annotation: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> int:
    """Add an item to an evaluation set."""
    init_eval_db()
    with get_eval_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO eval_items (eval_set, input_text, gold_annotation, metadata) VALUES (?, ?, ?, ?)",
            (eval_set, input_text, json.dumps(gold_annotation, ensure_ascii=False), json.dumps(metadata or {}))
        )
        conn.commit()
        # Update item count
        conn.execute(
            "UPDATE eval_sets SET item_count = (SELECT COUNT(*) FROM eval_items WHERE eval_set = ?) WHERE name = ?",
            (eval_set, eval_set)
        )
        conn.commit()
        return cursor.lastrowid


def get_eval_set(name: str) -> EvalSet | None:
    """Get evaluation set metadata."""
    init_eval_db()
    with get_eval_connection() as conn:
        row = conn.execute("SELECT * FROM eval_sets WHERE name = ?", (name,)).fetchone()
        if row:
            return EvalSet(**dict(row))
    return None


def list_eval_sets() -> list[EvalSet]:
    """List all evaluation sets."""
    init_eval_db()
    with get_eval_connection() as conn:
        rows = conn.execute("SELECT * FROM eval_sets ORDER BY created_at DESC").fetchall()
        return [EvalSet(**dict(row)) for row in rows]


def get_eval_items(eval_set: str, limit: int | None = None) -> list[EvalItem]:
    """Get items from an evaluation set."""
    init_eval_db()
    with get_eval_connection() as conn:
        query = "SELECT * FROM eval_items WHERE eval_set = ?"
        params = [eval_set]
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [EvalItem(**dict(row)) for row in rows]


def load_gold_from_jsonl(filepath: Path, eval_set: str, task: str, description: str) -> int:
    """Load gold annotations from JSONL file into eval database (one-time migration)."""
    if not filepath.exists():
        return 0

    create_eval_set(eval_set, task, description)
    count = 0

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            add_eval_item(
                eval_set=eval_set,
                input_text=item.get('text', ''),
                gold_annotation=item,
                metadata={'source': item.get('source', ''), 'sentence_id': item.get('sentence_id', '')}
            )
            count += 1

    return count


def export_eval_set_to_jsonl(eval_set: str, filepath: Path) -> int:
    """Export evaluation set to JSONL file (for backup/portability)."""
    items = get_eval_items(eval_set)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, 'w', encoding='utf-8') as f:
        for item in items:
            ann = json.loads(item.gold_annotation)
            ann['metadata'] = json.loads(item.metadata) if item.metadata else {}
            f.write(json.dumps(ann, ensure_ascii=False) + '\n')

    return len(items)


# Pre-defined gold set names (eval set name, task, description)
GOLD_SETS = {
    "tokenization": ("tokenization_gold_v0", "Tokenization gold set v0"),
    "pos": ("pos_gold_v0", "POS tagging gold set v0"),
    "morph": ("morph_gold_v0", "Morphology gold set v0"),
    "grammar": ("grammar_gold_v0", "Grammar error detection gold set v0"),
    "pos_gold_v0": ("pos_gold_v0", "POS tagging gold set v0"),
    "morph_gold_v0": ("morph_gold_v0", "Morphology gold set v0"),
    "grammar_gold_v0": ("grammar_gold_v0", "Grammar error detection gold set v0"),
    "tokenization_gold_v0": ("tokenization_gold_v0", "Tokenization gold set v0"),
}
