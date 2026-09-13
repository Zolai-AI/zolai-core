"""Extended repositories for DB-first tables introduced by the migration.

These back the runtime serving modules that previously read raw JSONL. Each
repository subclasses :class:`BaseRepository` and exposes the query helpers the
serving layer needs.

To wire one up: construct with an engine (use ``get_engine()``) and pass to the
serving module in place of ``json_lines.open(...)``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseRepository


class NgramRepository(BaseRepository):
    """Repository over the ``ngram`` table (unigram + bigram counts).

    Mirrors the previous ``ngrams.jsonl`` shape so ``knowledge.ngram`` can read
    prediction data straight from SQLite.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "ngram", id_column="id")

    def as_tables(self) -> dict[str, dict]:
        """Return ``{"unigrams": {word: count}, "bigrams": {(a, b): count}}``."""
        unigrams: dict[str, int] = {}
        bigrams: dict[tuple[str, str], int] = {}
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().with_only_columns(
                    self.table.c.ngram_type,
                    self.table.c.word,
                    self.table.c.a,
                    self.table.c.b,
                    self.table.c.count,
                )
            ).fetchall()
        for r in rows:
            if r.ngram_type == "bigram":
                bigrams[(r.a, r.b)] = r.count
            elif r.ngram_type == "unigram":
                unigrams[r.word] = r.count
        return {"unigrams": unigrams, "bigrams": bigrams}

    def top_unigrams(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.find(
            filters={"ngram_type": "unigram"},
            limit=limit,
            order_by="-count",
        )

    def save_tables(self, tables: dict[str, dict], batch_id: str = "manual") -> int:
        """Persist unigram/bigram tables (wipes previous rows for this source)."""
        with self._engine.begin() as conn:
            conn.execute(self.table.delete())
            count = 0
            for (a, b), c in tables.get("bigrams", {}).items():
                conn.execute(
                    self.table.insert(),
                    {
                        "ngram_type": "bigram",
                        "a": a,
                        "b": b,
                        "count": int(c),
                        "import_batch_id": batch_id,
                        "source_file": "ngram:memory",
                        "version": 1,
                    },
                )
                count += 1
            for w, c in tables.get("unigrams", {}).items():
                conn.execute(
                    self.table.insert(),
                    {
                        "ngram_type": "unigram",
                        "word": w,
                        "count": int(c),
                        "import_batch_id": batch_id,
                        "source_file": "ngram:memory",
                        "version": 1,
                    },
                )
                count += 1
        return count


class KnowledgeVectorRepository(BaseRepository):
    """Repository over the ``knowledge_vectors`` table.

    Mirrors the ``knowledge_vectors.jsonl`` record shape (id, text, metadata,
    embedding) so ``knowledge.retrieve`` can load the index from SQLite.
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "knowledge_vectors", id_column="id")

    def all_rows(self) -> list[dict[str, Any]]:
        return self.find(limit=1_000_000)

    def ids(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().with_only_columns(self.table.c.id)
            ).fetchall()
        return [r.id for r in rows]

    def texts(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().with_only_columns(self.table.c.text)
            ).fetchall()
        return [r.text for r in rows]

    def embedding_bytes(self) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select().with_only_columns(self.table.c.embedding)
            ).fetchall()
        return [r.embedding or "[]" for r in rows]

    def upsert_batch(
        self, records: Iterable[dict[str, Any]], batch_id: str = "manual"
    ) -> int:
        """Insert-or-replace knowledge vector rows, overwriting by ``id``."""
        count = 0
        with self._engine.begin() as conn:
            for rec in records:
                row = {
                    k: rec[k]
                    for k in ("id", "text", "metadata", "embedding",
                              "source_type", "source")
                    if k in rec
                }
                row["metadata"] = json.dumps(
                    rec.get("metadata") or {}, ensure_ascii=False
                )
                row["embedding"] = json.dumps(
                    rec.get("embedding") or [], ensure_ascii=False
                )
                row.update(
                    {
                        "import_batch_id": batch_id,
                        "version": 1,
                        "source_file": "knowledge:ingest",
                    }
                )
                conn.execute(
                    text(
                        "INSERT INTO knowledge_vectors (id, text, metadata, "
                        "embedding, source_type, source, import_batch_id, "
                        "source_file, version, imported_at) "
                        "VALUES (:id, :text, :metadata, :embedding, "
                        ":source_type, :source, :import_batch_id, "
                        ":source_file, :version, :imported_at) "
                        "ON CONFLICT(id) DO UPDATE SET "
                        "text=excluded.text, metadata=excluded.metadata, "
                        "embedding=excluded.embedding, source_type=excluded.source_type, "
                        "source=excluded.source, version=excluded.version"
                    ),
                    row,
                )
                count += 1
        return count


class ParticleRepository(BaseRepository):
    """Repository over the ``particle_database`` table."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "particle_database", id_column="id")

    def get_by_particle(self, particle: str) -> list[dict[str, Any]]:
        return self.find({"particle": particle})

    def get_by_function(self, function: str) -> list[dict[str, Any]]:
        return self.find({"function": function})

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.particle.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]


class VerbRepository(BaseRepository):
    """Repository over the ``verb_database`` table."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "verb_database", id_column="id")

    def get_by_verb(self, verb: str) -> list[dict[str, Any]]:
        return self.find({"verb": verb})

    def get_by_class(self, verb_class: str) -> list[dict[str, Any]]:
        return self.find({"verb_class": verb_class}, limit=200)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        with self._engine.connect() as conn:
            rows = conn.execute(
                self.table.select()
                .where(self.table.c.verb.ilike(pattern))
                .limit(limit)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]


class TrainingValidationRepository(BaseRepository):
    """Repository over the ``training_validation`` table."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "training_validation", id_column="id")

    def record_evaluation(
        self,
        exercise_id: int | None,
        level: str | None,
        prompt: str,
        expected: str,
        predicted: str,
    ) -> int:
        """Store one model-vs-gold prediction check; returns the row id."""
        import datetime

        return self.create(
            {
                "exercise_id": exercise_id,
                "level": level,
                "prompt": prompt,
                "expected": expected,
                "predicted": predicted,
                "is_valid": 1 if expected.strip() == predicted.strip() else 0,
                "error_codes": "[]",
                "checked_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
            }
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.find(limit=limit, order_by="-id")


class SimbuRepository(BaseRepository):
    """Repository over the ``simbu`` table (song/poetry text corpus)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "simbu", id_column="id")

    def get_by_type(self, type_: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.find({"type": type_}, limit=limit)

    def count_by_type(self) -> dict[str, int]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT COALESCE(type, '') AS t, COUNT(*) AS c "
                    "FROM simbu GROUP BY t"
                )
            ).fetchall()
        return {r.t: r.c for r in rows}


class GrammarInstructionRepository(BaseRepository):
    """Repository over the ``grammar_instructions`` table."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "grammar_instructions", id_column="id")

    def get_by_instruction(self, instruction: str) -> dict[str, Any] | None:
        return self.find_one({"instruction": instruction})


class CorrectionRepository(BaseRepository):
    """Repository over the ``corrections`` (user proposal) table."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine, "corrections", id_column="id")

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.find({"status": "pending"}, limit=limit, order_by="proposed_at")

    def for_table(self, table_name: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.find({"table_name": table_name}, limit=limit, order_by="-id")


_REPO_CLASSES = {
    "ngram": NgramRepository,
    "knowledge_vectors": KnowledgeVectorRepository,
    "particle_database": ParticleRepository,
    "verb_database": VerbRepository,
    "training_validation": TrainingValidationRepository,
    "simbu": SimbuRepository,
    "grammar_instructions": GrammarInstructionRepository,
    "corrections": CorrectionRepository,
}
