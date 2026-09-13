"""
JSONL Import/Export Pipeline for Zolai DB.

Scans JSONL files, imports into SQLite with version tracking,
and exports clean JSONL from DB.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import text, Column, DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ─── Configuration ────────────────────────────────────────────────────────

DATA_ROOT = Path("/home/peter/Documents/Projects/zolai-ai/data")
DB_PATH = DATA_ROOT / "zolai.db"

# Canonical JSONL files to import (relative to DATA_ROOT)
CANONICAL_JSONL_FILES = {
    # Bible
    "bible/parallel_corpus_v1.jsonl": "bible_verses",
    "bible/word_alignments_v1.jsonl": "word_alignments",
    "bible/grammar_patterns_v2.jsonl": "grammar_patterns",
    "bible/phrases_v1.jsonl": "phrases",
    "bible/vocab_index_full.jsonl": "vocab",
    "bible/vocab_from_bible.jsonl": "zolai_vocabulary",
    "bible/phrases_from_bible.jsonl": "phrases_from_bible",
    "bible/translation_pairs_v1.jsonl": "translations",
    "bible/negation_exercises.jsonl": "training_exercises",
    "bible/question_exercises.jsonl": "training_exercises",
    "bible/pronoun_exercises.jsonl": "training_exercises",
    "bible/error_correction_exercises.jsonl": "training_exercises",
    "bible/conditional_exercises.jsonl": "training_exercises",
    "bible/proverbs.jsonl": "proverbs",
    "bible/word_collocations.jsonl": "word_collocations",
    "bible/word_usage_profiles.jsonl": "word_usage",
    
    # Dictionary
    "dictionary/processed/dict_zo_en_master_v1.jsonl": "dictionary",
    "dictionary/processed/dict_canonical_clean.jsonl": "dictionary_en_zo",
    "dictionary/processed/dict__merged.jsonl": "dictionary",
    "dictionary/processed/dict_bible_combined_v1.jsonl": "dictionary",
    "dictionary/processed/dict_corrections.jsonl": "zvs_corrections",
    "dictionary/processed/phrases_verified.jsonl": "phrases",
    "dictionary/processed/sentence_patterns_verified.jsonl": "grammar_patterns",
    "dictionary/processed/vocab_verified.jsonl": "vocab",
    
    # Training
    "training/pipeline_output/training_corpus_qwen3.jsonl": "training_corpus_qwen3",
    "training/pipeline_output/valid_sentences.jsonl": "training_valid_sentences",
    "training/seed_data_500_fixed.jsonl": "training_seed_data",
    
    # Context deep learning
    "bible/context/per_book_analysis.jsonl": "bible_book_analysis",
    "bible/context/per_chapter_analysis.jsonl": "bible_chapter_analysis",
    "bible/context/phrase_context_map.jsonl": "phrase_context",
    "bible/context/topic_clusters.jsonl": "topic_clusters",
    "bible/context/sentence_patterns.jsonl": "sentence_patterns",
    "bible/context/word_usage_profiles.jsonl": "word_usage_profiles",
    
    # Parallel corpora
    "parallel/zo_en_pairs_combined_v1.jsonl": "translations",
    
    # Online data
    "processed/my/dict_zo_my_v1.jsonl": "dictionary_my",
    "processed/my/dict_my_zo_v1.jsonl": "dictionary_en_my",
    "processed/my/dict_trilingual_v1.jsonl": "dictionary_trilingual",
}

# ─── SQLAlchemy Setup ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class ImportVersionMixin:
    """Mixin to add version tracking columns to imported tables."""
    import_batch_id = Column(String(36), nullable=True, index=True)
    source_file = Column(String(500), nullable=True)
    version = Column(Integer, nullable=True, default=1)
    imported_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))


class JSONLImportLog(Base):
    """Log of JSONL import operations."""
    __tablename__ = "jsonl_import_log"
    
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
    """Main pipeline for JSONL import/export with version tracking."""
    
    def __init__(self, db_path: Path = DB_PATH, data_root: Path = DATA_ROOT):
        self.db_path = db_path
        self.data_root = data_root
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
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
                        record["import_batch_id"] = batch_id
                        record["source_file"] = str(file_path.relative_to(self.data_root))
                        record["version"] = version
                        record["imported_at"] = datetime.now(timezone.utc).isoformat()
                        
                        cols = list(record.keys())
                        placeholders = ", ".join([f":{c}" for c in cols])
                        col_names = ", ".join([f'"{c}"' for c in cols])
                        sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                        session.execute(text(sql), record)
                        rows_imported += 1
                        
                        if rows_imported % 1000 == 0:
                            session.commit()
                    except json.JSONDecodeError as e:
                        print(f"  Warning: JSON decode error at line {line_num}: {e}")
                        continue
                    except Exception as e:
                        print(f"  Warning: Insert error at line {line_num}: {e}")
                        continue
            
            session.commit()
            
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
            query = f'SELECT * FROM "{table_name}"'
            conditions = []
            params = {}
            
            if batch_id:
                conditions.append('import_batch_id = :batch_id')
                params["batch_id"] = batch_id
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
            query = "SELECT DISTINCT table_name FROM jsonl_import_log"
            if batch_id:
                query += " WHERE batch_id = :batch_id"
                result = session.execute(text(query), {"batch_id": batch_id})
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
                FROM jsonl_import_log
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
    
    parser = argparse.ArgumentParser(description="Zolai JSONL Import/Export Pipeline")
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
