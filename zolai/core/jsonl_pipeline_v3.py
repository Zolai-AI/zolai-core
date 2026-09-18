"""
JSONL Import/Export Pipeline for Zolai DB - Schema Evolution Version.

Dynamically creates/alters tables to match JSONL structure with version tracking.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ─── Configuration ────────────────────────────────────────────────────────
from zolai.config import config

DATA_ROOT = config.paths.data
DB_PATH = config.paths.zolai_db

CANONICAL_JSONL_FILES = {
    # Bible
    "bible/parallel_corpus_v1.jsonl": "bible_verses_import",
    "bible/word_alignments_v1.jsonl": "word_alignments_import",
    "bible/grammar_patterns_v2.jsonl": "grammar_patterns_import",
    "bible/phrases_v1.jsonl": "phrases_import",
    "bible/vocab_index_full.jsonl": "vocab_import",
    "bible/vocab_from_bible.jsonl": "zolai_vocabulary_import",
    "bible/phrases_from_bible.jsonl": "phrases_from_bible_import",
    "bible/translation_pairs_v1.jsonl": "translations_import",
    "bible/negation_exercises.jsonl": "training_exercises_import",
    "bible/question_exercises.jsonl": "training_exercises_import",
    "bible/pronoun_exercises.jsonl": "training_exercises_import",
    "bible/error_correction_exercises.jsonl": "training_exercises_import",
    "bible/conditional_exercises.jsonl": "training_exercises_import",
    "bible/proverbs.jsonl": "proverbs_import",
    "bible/word_collocations.jsonl": "word_collocations_import",
    "bible/word_usage_profiles.jsonl": "word_usage_import",

    # Dictionary
    "dictionary/processed/dict_zo_en_master_v1.jsonl": "dictionary_import",
    "dictionary/processed/dict_canonical_clean.jsonl": "dictionary_en_zo_import",
    "dictionary/processed/dict__merged.jsonl": "dictionary_import",
    "dictionary/processed/dict_bible_combined_v1.jsonl": "dictionary_import",
    "dictionary/processed/dict_corrections.jsonl": "zvs_corrections_import",
    "dictionary/processed/phrases_verified.jsonl": "phrases_import",
    "dictionary/processed/sentence_patterns_verified.jsonl": "grammar_patterns_import",
    "dictionary/processed/vocab_verified.jsonl": "vocab_import",

    # Training
    "training/pipeline_output/training_corpus_qwen3.jsonl": "training_corpus_qwen3_import",
    "training/pipeline_output/valid_sentences.jsonl": "training_valid_sentences_import",
    "training/seed_data_500_fixed.jsonl": "training_seed_data_import",

    # Context deep learning
    "bible/context/per_book_analysis.jsonl": "bible_book_analysis_import",
    "bible/context/per_chapter_analysis.jsonl": "bible_chapter_analysis_import",
    "bible/context/phrase_context_map.jsonl": "phrase_context_import",
    "bible/context/topic_clusters.jsonl": "topic_clusters_import",
    "bible/context/sentence_patterns.jsonl": "sentence_patterns_import",
    "bible/context/word_usage_profiles.jsonl": "word_usage_profiles_import",

    # Parallel corpora
    "parallel/zo_en_pairs_combined_v1.jsonl": "translations_import",

    # Online data
    "processed/my/dict_zo_my_v1.jsonl": "dictionary_my_import",
    "processed/my/dict_my_zo_v1.jsonl": "dictionary_en_my_import",
    "processed/my/dict_trilingual_v1.jsonl": "dictionary_trilingual_import",
}

# ─── SQLAlchemy Setup ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class JSONLImportLog(Base):
    """Log of JSONL import operations."""
    __tablename__ = "import_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(36), nullable=False, index=True)
    source_file = Column(String(500), nullable=False)
    table_name = Column(String(100), nullable=False, index=True)
    rows_imported = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="completed")
    error_message = Column(Text, nullable=True)


# ─── Core Pipeline Class ──────────────────────────────────────────────────

class JSONLPipeline:
    """Dynamic pipeline with schema evolution support."""

    def __init__(self, db_path: Path = DB_PATH, data_root: Path = DATA_ROOT):
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine, tables=[JSONLImportLog.__table__])

    def _get_session(self) -> Session:
        return self.Session()

    def _compute_sha256(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _generate_batch_id(self) -> str:
        return str(uuid.uuid4())

    def discover_jsonl_files(self) -> dict[str, Path]:
        found = {}
        for rel_path, table_name in CANONICAL_JSONL_FILES.items():
            full_path = self.data_root / rel_path
            if full_path.exists():
                found[table_name] = full_path
        return found

    def _infer_sql_type(self, value: Any) -> str:
        """Infer SQLite type from Python value."""
        if isinstance(value, bool):
            return "INTEGER"
        elif isinstance(value, int):
            return "INTEGER"
        elif isinstance(value, float):
            return "REAL"
        elif isinstance(value, (dict, list)):
            return "TEXT"
        else:
            return "TEXT"

    def _ensure_table_exists(self, session: Session, table_name: str, sample_record: dict):
        """Create table dynamically based on sample record keys."""
        inspector = sa_inspect(self.engine)
        if table_name in inspector.get_table_names():
            return

        cols = []
        for key, value in sample_record.items():
            col_type = self._infer_sql_type(value)
            cols.append(f'"{key}" {col_type}')

        cols.extend([
            '"import_batch_id" TEXT',
            '"source_file" TEXT',
            '"version" INTEGER DEFAULT 1',
            '"imported_at" TEXT'
        ])

        create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n  ' + ',\n  '.join(cols) + '\n)'
        session.execute(text(create_sql))
        session.execute(text(f'CREATE INDEX IF NOT EXISTS idx_{table_name}_batch ON "{table_name}"(import_batch_id)'))
        session.commit()
        print(f"  Created table: {table_name}")

    def _ensure_columns_exist(self, session: Session, table_name: str, record: dict):
        """Add missing columns to table dynamically."""
        inspector = sa_inspect(self.engine)
        if table_name not in inspector.get_table_names():
            return

        existing_cols = {col['name'] for col in inspector.get_columns(table_name)}
        new_cols = set(record.keys()) - existing_cols - {"import_batch_id", "source_file", "version", "imported_at", "id"}

        for col_name in new_cols:
            # Get a sample value to infer type
            sample_val = record[col_name]
            col_type = self._infer_sql_type(sample_val)
            try:
                session.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {col_type}'))
                session.commit()
                print(f"  Added column: {table_name}.{col_name} ({col_type})")
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    print(f"  Warning: Could not add column {col_name}: {e}")

    def import_file(self, file_path: Path, table_name: str, batch_id: str | None = None, version: int = 1) -> dict:
        if batch_id is None:
            batch_id = self._generate_batch_id()

        sha256 = self._compute_sha256(file_path)
        session = self._get_session()
        rows_imported = 0

        try:
            with file_path.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)

                        # Ensure table exists (on first record)
                        if rows_imported == 0:
                            self._ensure_table_exists(session, table_name, record)

                        # Ensure all columns exist for this record
                        self._ensure_columns_exist(session, table_name, record)

                        # Add version tracking fields
                        record["import_batch_id"] = batch_id
                        record["source_file"] = str(file_path.relative_to(self.data_root))
                        record["version"] = version
                        record["imported_at"] = datetime.now(timezone.utc).isoformat()

                        # Convert complex types to JSON strings
                        for k, v in record.items():
                            if isinstance(v, (dict, list)):
                                record[k] = json.dumps(v, ensure_ascii=False)

                        cols = list(record.keys())
                        placeholders = ", ".join([f":{c}" for c in cols])
                        col_names = ", ".join([f'"{c}"' for c in cols])
                        sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                        session.execute(text(sql), record)
                        rows_imported += 1

                        if rows_imported % 1000 == 0:
                            session.commit()
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        print(f"  Warning: Insert error at line {line_num}: {e}")
                        continue

            session.commit()

            # Log the import
            log_entry = JSONLImportLog(
                batch_id=batch_id,
                source_file=str(file_path.relative_to(self.data_root)),
                table_name=table_name,
                rows_imported=rows_imported,
                sha256=sha256,
                version=1,
                status="completed"
            )
            session.add(log_entry)
            session.commit()

            return {
                "batch_id": batch_id,
                "table": table_name,
                "file": str(file_path),
                "rows_imported": rows_imported,
                "sha256": sha256,
                "status": "completed"
            }

        except Exception as e:
            session.rollback()
            log_entry = JSONLImportLog(
                batch_id=batch_id,
                source_file=str(file_path.relative_to(self.data_root)),
                table_name=table_name,
                rows_imported=0,
                sha256=sha256,
                version=version,
                status="failed",
                error_message=str(e)
            )
            session.add(log_entry)
            session.commit()
            raise
        finally:
            session.close()

    def import_all(self, batch_id: str | None = None) -> list[dict]:
        if batch_id is None:
            batch_id = self._generate_batch_id()

        results = []
        files = self.discover_jsonl_files()
        print(f"Found {len(files)} JSONL files to import")

        for table_name, file_path in files.items():
            print(f"Importing {file_path.relative_to(self.data_root)} → {table_name}...")
            try:
                result = self.import_file(file_path, table_name, batch_id)
                results.append(result)
                print(f"  ✓ {result['rows_imported']} rows")
            except Exception as e:
                print(f"  ✗ Failed: {e}")
                results.append({
                    "batch_id": batch_id,
                    "table": table_name,
                    "file": str(file_path),
                    "rows_imported": 0,
                    "status": "failed",
                    "error": str(e)
                })

        return results

    def export_table(self, table_name: str, output_path: Path, batch_id: str | None = None,
                     version: int | None = None, clean: bool = False, limit: int | None = None) -> dict:
        session = self._get_session()

        try:
            inspector = sa_inspect(self.engine)
            if table_name not in inspector.get_table_names():
                raise ValueError(f"Table {table_name} does not exist")

            query = f'SELECT * FROM "{table_name}"'
            conditions = []
            params = {}

            if batch_id:
                conditions.append('import_batch_id = :batch_id')
                params['batch_id'] = batch_id
            if version:
                conditions.append('version = :version')
                params['version'] = version

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            if limit:
                query += f" LIMIT {limit}"

            result = session.execute(text(query), params)
            rows = result.fetchall()
            cols = list(result.keys()) if result.keys() else []

            output_path.parent.mkdir(parents=True, exist_ok=True)
            written = 0

            seen = set() if clean else None

            with output_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    record = dict(zip(cols, row))

                    if clean:
                        for field in ["import_batch_id", "source_file", "version", "imported_at"]:
                            record.pop(field, None)

                        core = {k: v for k, v in record.items()
                               if k not in ["import_batch_id", "source_file", "version", "imported_at", "id"]}
                        core_hash = hashlib.md5(json.dumps(core, sort_keys=True).encode()).hexdigest()
                        if core_hash in seen:
                            continue
                        seen.add(core_hash)

                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    written += 1

            return {
                "table": table_name,
                "output": str(output_path),
                "rows_written": written,
                "batch_id": batch_id,
                "clean": clean
            }

        finally:
            session.close()

    def export_all(self, output_dir: Path, batch_id: str | None = None,
                   version: int | None = None, clean: bool = False) -> list[dict]:
        session = self._get_session()

        try:
            query = "SELECT DISTINCT table_name FROM import_log"
            if batch_id:
                result = session.execute(text(query + " WHERE batch_id = :batch_id"), {"batch_id": batch_id})
            else:
                result = session.execute(text(query))

            tables = [row[0] for row in result]
            results = []

            for table_name in tables:
                output_path = output_dir / f"{table_name}.jsonl"
                result = self.export_table(table_name, output_path, batch_id, version, clean)
                results.append(result)
                print(f"Exported {table_name}: {result['rows_written']} rows → {output_path}")

            return results

        finally:
            session.close()

    def get_import_log(self, limit: int = 50) -> list[dict]:
        session = self._get_session()
        try:
            query = """
                SELECT batch_id, source_file, table_name, rows_imported, sha256,
                       imported_at, version, status, error_message
                FROM import_log
                ORDER BY imported_at DESC
                LIMIT :limit
            """
            result = session.execute(text(query), {"limit": limit})
            cols = ["batch_id", "source_file", "table_name", "rows_imported", "sha256",
                   "imported_at", "version", "status", "error_message"]
            return [dict(zip(cols, row)) for row in result]
        finally:
            session.close()


# ─── CLI Entry Point ──────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Zolai JSONL Import/Export Pipeline v3")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_import_all = subparsers.add_parser("import-all", help="Import all canonical JSONL files")
    p_import_all.add_argument("--batch-id", help="Custom batch ID (default: auto)")

    p_import = subparsers.add_parser("import", help="Import a single JSONL file")
    p_import.add_argument("file", help="Path to JSONL file")
    p_import.add_argument("--table", required=True, help="Target table name")
    p_import.add_argument("--batch-id", help="Custom batch ID")
    p_import.add_argument("--version", type=int, default=1, help="Version number")

    p_export = subparsers.add_parser("export", help="Export a table to JSONL")
    p_export.add_argument("--table", required=True, help="Table name")
    p_export.add_argument("--output", required=True, help="Output JSONL path")
    p_export.add_argument("--batch-id", help="Filter by batch ID")
    p_export.add_argument("--version", type=int, help="Filter by version")
    p_export.add_argument("--clean", action="store_true", help="Clean export (dedupe, strip metadata)")
    p_export.add_argument("--limit", type=int, help="Limit rows")

    p_export_all = subparsers.add_parser("export-all", help="Export all tables")
    p_export_all.add_argument("--output-dir", required=True, help="Output directory")
    p_export_all.add_argument("--batch-id", help="Filter by batch ID")
    p_export_all.add_argument("--version", type=int, help="Filter by version")
    p_export_all.add_argument("--clean", action="store_true", help="Clean export")

    p_log = subparsers.add_parser("log", help="Show import log")
    p_log.add_argument("--limit", type=int, default=50, help="Limit entries")

    args = parser.parse_args()

    pipeline = JSONLPipeline()

    if args.command == "import-all":
        batch_id = args.batch_id or pipeline._generate_batch_id()
        print(f"Starting import with batch_id: {batch_id}")
        results = pipeline.import_all(batch_id)
        for r in results:
            print(f"  {r['table']}: {r['rows_imported']} rows ({r['status']})")

    elif args.command == "import":
        result = pipeline.import_file(Path(args.file), args.table, args.batch_id, args.version)
        print(f"Imported {result['rows_imported']} rows to {result['table']} (batch: {result['batch_id']})")

    elif args.command == "export":
        result = pipeline.export_table(
            args.table, Path(args.output), args.batch_id, args.version, args.clean, args.limit
        )
        print(f"Exported {result['rows_written']} rows to {result['output']}")

    elif args.command == "export-all":
        results = pipeline.export_all(Path(args.output_dir), args.batch_id, args.version, args.clean)
        for r in results:
            print(f"  {r['table']}: {r['rows_written']} rows")

    elif args.command == "log":
        logs = pipeline.get_import_log(args.limit)
        for log in logs:
            print(f"{log['imported_at']} | {log['batch_id'][:8]} | {log['table_name']:30} | {log['rows_imported']:>8} rows | {log['status']}")


if __name__ == "__main__":
    main()
