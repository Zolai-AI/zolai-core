"""Unified data access layer for Zolai.

Provides DatabaseManager with SQLite and PostgreSQL backends.
Lazy-connection pattern: database opens on first query.

Usage:
    from zolai.data.database import get_manager
    mgr = get_manager()          # SQLite default
    mgr = get_manager("postgresql://...")  # PostgreSQL
    results = mgr.lookup_word("pasian")

CLI:
    python -m zolai.data.database status
    python -m zolai.data.database backup
    python -m zolai.data.database restore
    python -m zolai.data.database query "pasian"
    python -m zolai.data.database stats
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Engine

from .models import MODEL_REGISTRY, Base

logger = logging.getLogger(__name__)

# JSON column name → Python json.loads before returning
_JSON_COLS: dict[str, set[str]] = {
    "dictionary": {"english"},
    "bible_verses": set(),
    "grammar_patterns": {"examples"},
    "phrases": {"examples"},
    "vocab": {"books", "examples"},
    "translations": set(),
    "word_usage": {"meaning_shifts", "co_occurring_words"},
    "provenance": set(),
}


class DatabaseManager:
    """Unified database access with lazy connection."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or "sqlite:///zolai.db"
        self._engine: Engine | None = None
        self._metadata: MetaData | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            connect_args: dict[str, Any] = {}
            pool_kwargs: dict[str, Any] = {}
            if self._db_url.startswith("sqlite"):
                connect_args = {"check_same_thread": False}
            else:
                # PostgreSQL connection pooling
                pool_kwargs = {
                    "pool_size": 5,
                    "max_overflow": 10,
                    "pool_pre_ping": True,
                }
            self._engine = create_engine(
                self._db_url,
                echo=False,
                connect_args=connect_args,
                **pool_kwargs,
            )
            if self._db_url.startswith("sqlite"):
                with self._engine.connect() as conn:
                    conn.execute(text("PRAGMA journal_mode=WAL"))
                    conn.commit()
        return self._engine

    @property
    def metadata(self) -> MetaData:
        if self._metadata is None:
            self._metadata = MetaData()
            Base.metadata.reflect(bind=self.engine)
            self._metadata = Base.metadata
        return self._metadata

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def init_db(self) -> None:
        """Create all tables (idempotent)."""
        Base.metadata.create_all(self.engine)
        logger.info("Database initialized: %s", self._db_url)

    def table_names(self) -> list[str]:
        """Return sorted list of table names."""
        return sorted(Base.metadata.tables.keys())

    # ------------------------------------------------------------------
    # Lookup methods
    # ------------------------------------------------------------------
    def lookup_word(self, word: str) -> list[dict[str, Any]]:
        """Search dictionary by Zolai headword (case-insensitive)."""
        table = Table("dictionary", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    table.c.zolai.ilike(f"%{word}%")
                )
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def search_bible(self, query: str) -> list[dict[str, Any]]:
        """Search Bible verses by Zolai or English text (LIKE match)."""
        table = Table("bible_verses", self.metadata, autoload_with=self.engine)
        pattern = f"%{query}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.zo_tdb77.ilike(pattern))
                    | (table.c.zo_tedim2010.ilike(pattern))
                    | (table.c.en_kJV.ilike(pattern))
                ).limit(100)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def match_phrase(self, word: str) -> list[dict[str, Any]]:
        """Find phrases containing a word (case-insensitive LIKE)."""
        table = Table("phrases", self.metadata, autoload_with=self.engine)
        pattern = f"%{word}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.zo.ilike(pattern))
                    | (table.c.english.ilike(pattern))
                ).limit(50)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_grammar(self, pattern: str) -> list[dict[str, Any]]:
        """Look up grammar patterns by name or description."""
        table = Table("grammar_patterns", self.metadata, autoload_with=self.engine)
        pat = f"%{pattern}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.pattern.ilike(pat))
                    | (table.c.description.ilike(pat))
                ).limit(50)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_vocab(self, word: str) -> list[dict[str, Any]]:
        """Look up vocabulary entries by headword."""
        table = Table("vocab", self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    table.c.headword.ilike(f"%{word}%")
                )
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    def get_translations(
        self, word: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Look up translation pairs containing a word."""
        table = Table("translations", self.metadata, autoload_with=self.engine)
        pat = f"%{word}%"
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(
                    (table.c.source.ilike(pat))
                    | (table.c.target.ilike(pat))
                ).limit(limit)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    # ------------------------------------------------------------------
    # Count / aggregate
    # ------------------------------------------------------------------
    def count(self, table_name: str) -> int:
        """Return row count for a table."""
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        with self.engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            )
            return result.scalar_one()

    def counts(self) -> dict[str, int]:
        """Return row counts for all tables."""
        return {name: self.count(name) for name in self.table_names()}

    # ------------------------------------------------------------------
    # Bulk insert
    # ------------------------------------------------------------------
    def insert_many(self, table_name: str, records: list[dict[str, Any]]) -> int:
        """Bulk insert records into a table.

        JSON-serialises list/dict columns automatically.
        Returns count of inserted rows.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        json_cols = _JSON_COLS.get(table_name, set())
        cleaned = []
        for rec in records:
            row = {}
            for k, v in rec.items():
                if k in json_cols and isinstance(v, (list, dict)):
                    row[k] = json.dumps(v, ensure_ascii=False)
                elif k in json_cols and isinstance(v, str):
                    # Already a string — keep as-is
                    row[k] = v
                else:
                    row[k] = v
            cleaned.append(row)
        with self.engine.begin() as conn:
            conn.execute(table.insert(), cleaned)
        return len(cleaned)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_table(self, table_name: str) -> list[dict[str, Any]]:
        """Export all rows from a table as list of dicts.

        Deserialises JSON columns back to Python objects.
        """
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        json_cols = _JSON_COLS.get(table_name, set())
        with self.engine.connect() as conn:
            rows = conn.execute(table.select()).fetchall()
        result = []
        for row in rows:
            d = self._row_to_dict(row, table)
            for col in json_cols:
                if col in d and isinstance(d[col], str):
                    try:
                        d[col] = json.loads(d[col])
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: Any, table: Table) -> dict[str, Any]:
        """Convert a Row to a plain dict, skipping internal id."""
        cols = [c.name for c in table.columns if c.name != "id"]
        return {col: getattr(row, col, None) for col in cols}

    # ------------------------------------------------------------------
    # FTS5 full-text search
    # ------------------------------------------------------------------
    def _fts5_available(self) -> bool:
        """Check if FTS5 extension is available."""
        if not self._db_url.startswith("sqlite"):
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1 FROM dictionary_fts LIMIT 0"))
            return True
        except Exception:
            return False

    def create_fts5(self) -> None:
        """Create FTS5 virtual tables for dictionary and Bible search.

        SQLite-only. Creates FTS5 if not already present.
        """
        if not self._db_url.startswith("sqlite"):
            logger.warning("FTS5 only supported on SQLite")
            return
        with self.engine.connect() as conn:
            # Dictionary FTS5
            try:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS dictionary_fts "
                    "USING fts5(zolai, english, content='dictionary', "
                    "content_rowid='id')"
                ))
            except Exception as exc:
                logger.warning("Could not create dictionary_fts: %s", exc)
            # Bible FTS5
            try:
                conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS bible_fts "
                    "USING fts5(zo_tdb77, zo_tedim2010, en_kJV, "
                    "content='bible_verses', content_rowid='id')"
                ))
            except Exception as exc:
                logger.warning("Could not create bible_fts: %s", exc)
            conn.commit()
            logger.info("FTS5 virtual tables created")

    def _populate_fts5(self) -> None:
        """Rebuild FTS5 indexes from source tables."""
        if not self._db_url.startswith("sqlite"):
            return
        with self.engine.connect() as conn:
            try:
                conn.execute(text(
                    "INSERT INTO dictionary_fts(dictionary_fts) "
                    "VALUES('rebuild')"
                ))
            except Exception:
                pass
            try:
                conn.execute(text(
                    "INSERT INTO bible_fts(bible_fts) "
                    "VALUES('rebuild')"
                ))
            except Exception:
                pass
            conn.commit()

    def search_text(self, query: str) -> list[dict[str, Any]]:
        """Full-text search across dictionary and Bible using FTS5.

        Falls back to LIKE search if FTS5 is not available.
        Returns combined results from both tables.
        """
        results: list[dict[str, Any]] = []
        if self._fts5_available():
            # FTS5 dictionary search
            dict_table = Table(
                "dictionary", self.metadata, autoload_with=self.engine
            )
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT rowid, * FROM dictionary_fts "
                            "WHERE dictionary_fts MATCH :q LIMIT 50"
                        ),
                        {"q": query},
                    ).fetchall()
                for row in rows:
                    results.append({
                        "table": "dictionary",
                        "zolai": getattr(row, "zolai", None),
                        "english": getattr(row, "english", None),
                    })
            except Exception:
                pass

            # FTS5 Bible search
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        text(
                            "SELECT rowid, * FROM bible_fts "
                            "WHERE bible_fts MATCH :q LIMIT 50"
                        ),
                        {"q": query},
                    ).fetchall()
                for row in rows:
                    results.append({
                        "table": "bible_verses",
                        "zo_tdb77": getattr(row, "zo_tdb77", None),
                        "en_kJV": getattr(row, "en_kJV", None),
                    })
            except Exception:
                pass
        else:
            # Fallback: LIKE search
            results.extend(
                self._like_search("dictionary", ["zolai", "english"], query)
            )
            results.extend(
                self._like_search(
                    "bible_verses",
                    ["zo_tdb77", "zo_tedim2010", "en_kJV"],
                    query,
                )
            )
        return results

    def _like_search(
        self,
        table_name: str,
        columns: list[str],
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fallback LIKE search for a single table."""
        table = Table(table_name, self.metadata, autoload_with=self.engine)
        pattern = f"%{query}%"
        col_conditions = [
            getattr(table.c, col).ilike(pattern) for col in columns
        ]
        combined = col_conditions[0]
        for cond in col_conditions[1:]:
            combined = combined | cond
        with self.engine.connect() as conn:
            rows = conn.execute(
                table.select().where(combined).limit(limit)
            ).fetchall()
        return [self._row_to_dict(row, table) for row in rows]

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------
    def backup(self, backup_path: str | Path | None = None) -> Path:
        """Copy current database to a backup file using SQLite backup API.

        Args:
            backup_path: Where to write backup.
                Defaults to <db_name>.bak next to the DB file.

        Returns:
            Path to the backup file.
        """
        db_file = self._get_db_path()
        if db_file is None:
            raise RuntimeError(
                "Backup only supported for file-based SQLite"
            )
        if backup_path is None:
            backup_path = db_file.with_suffix(".db.bak")
        else:
            backup_path = Path(backup_path)

        import sqlite3

        src = sqlite3.connect(str(db_file))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        logger.info("Backed up %s → %s", db_file, backup_path)
        return backup_path

    def restore(self, backup_path: str | Path | None = None) -> Path:
        """Restore database from a backup file.

        Args:
            backup_path: Source backup. Defaults to <db_name>.bak.

        Returns:
            Path restored from.
        """
        db_file = self._get_db_path()
        if db_file is None:
            raise RuntimeError(
                "Restore only supported for file-based SQLite"
            )
        if backup_path is None:
            backup_path = db_file.with_suffix(".db.bak")
        else:
            backup_path = Path(backup_path)
        if not Path(backup_path).exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")

        import sqlite3

        # Fully dispose to release pooled connections
        self.dispose()

        # Use SQLite backup API for clean restore
        src = sqlite3.connect(str(backup_path))
        dst = sqlite3.connect(str(db_file))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # Remove WAL/SHM files left from old connections
        for suffix in ("-wal", "-shm"):
            stale = db_file.parent / (db_file.name + suffix)
            if stale.exists():
                stale.unlink()

        logger.info("Restored %s → %s", backup_path, db_file)
        return Path(backup_path)

    def _get_db_path(self) -> Path | None:
        """Extract file path from SQLite URL, or None for non-SQLite."""
        if not self._db_url.startswith("sqlite"):
            return None
        # sqlite:///path/to/file.db → /path/to/file.db
        path_str = self._db_url.replace("sqlite:///", "")
        return Path(path_str)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    def health_check(self) -> dict[str, Any]:
        """Return database health metrics.

        Returns dict with:
            db_url: Database URL (masked)
            backend: "sqlite" or "postgresql"
            table_counts: {table_name: row_count}
            total_rows: sum of all counts
            db_size_bytes: file size (SQLite) or 0
            db_size_human: human-readable size
            fts5_available: bool
            query_latency_ms: time for a COUNT query
            tables: list of table names
        """
        backend = "sqlite" if self._db_url.startswith("sqlite") else "postgresql"
        counts = self.counts()
        total = sum(counts.values())

        # DB size
        db_path = self._get_db_path()
        db_size = 0
        if db_path and db_path.exists():
            db_size = db_path.stat().st_size

        # Query latency
        t0 = time.monotonic()
        try:
            self.count("dictionary")
        except Exception:
            pass
        latency_ms = (time.monotonic() - t0) * 1000

        # Mask URL
        masked_url = self._db_url
        if "@" in masked_url:
            masked_url = masked_url.split("@")[-1]

        return {
            "db_url": masked_url,
            "backend": backend,
            "table_counts": counts,
            "total_rows": total,
            "db_size_bytes": db_size,
            "db_size_human": _human_size(db_size),
            "fts5_available": self._fts5_available(),
            "query_latency_ms": round(latency_ms, 2),
            "tables": self.table_names(),
        }

    def status(self) -> str:
        """Return a human-readable status string."""
        info = self.health_check()
        lines = [
            f"Backend: {info['backend']}",
            f"URL: {info['db_url']}",
            f"Size: {info['db_size_human']}",
            f"Tables: {len(info['tables'])}",
            f"Total rows: {info['total_rows']:,}",
            f"FTS5: {'available' if info['fts5_available'] else 'not available'}",
            f"Query latency: {info['query_latency_ms']}ms",
            "",
            "Table counts:",
        ]
        for name, count in info["table_counts"].items():
            lines.append(f"  {name}: {count:,}")
        return "\n".join(lines)

    def dispose(self) -> None:
        """Release connection pool resources."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._metadata = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _human_size(n: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------
_manager: DatabaseManager | None = None


def get_manager(db_url: str | None = None) -> DatabaseManager:
    """Get or create a DatabaseManager singleton.

    If db_url is provided, creates a new manager (replaces any existing).
    If not provided and no singleton exists, checks ZOLAI_PG_URL env var.
    If ZOLAI_PG_URL is set, uses PostgreSQL; otherwise uses SQLite.
    """
    global _manager
    if db_url is not None or _manager is None:
        if _manager is not None:
            _manager.dispose()
        if db_url is None:
            db_url = os.environ.get("ZOLAI_PG_URL") or "sqlite:///zolai.db"
        _manager = DatabaseManager(db_url)
    return _manager


def init_db(db_url: str | None = None) -> DatabaseManager:
    """Create tables and return the manager."""
    mgr = get_manager(db_url)
    mgr.init_db()
    return mgr


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for database management.

    Commands:
        status   — show table counts, DB size, last migration
        backup   — copy DB to <name>.db.bak
        restore  — restore from backup
        query    — quick word lookup (dictionary + bible)
        stats    — show index sizes, query performance, FTS5 status
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Zolai database management CLI",
    )
    parser.add_argument(
        "--db",
        default="sqlite:///zolai.db",
        help="Database URL",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show database status")
    sub.add_parser("backup", help="Backup database to .bak file")
    sub.add_parser("restore", help="Restore database from .bak file")
    sub.add_parser("stats", help="Show detailed statistics")

    query_p = sub.add_parser("query", help="Quick word lookup")
    query_p.add_argument("text", help="Word or phrase to search")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    mgr = get_manager(args.db)
    mgr.init_db()

    if args.command == "status":
        print(mgr.status())

    elif args.command == "backup":
        path = mgr.backup()
        print(f"Backed up to: {path}")

    elif args.command == "restore":
        path = mgr.restore()
        print(f"Restored from: {path}")

    elif args.command == "query":
        text_val = args.text
        print(f"Looking up: {text_val}\n")

        # Dictionary
        dict_results = mgr.lookup_word(text_val)
        if dict_results:
            print(f"Dictionary ({len(dict_results)} results):")
            for r in dict_results[:10]:
                print(f"  {r['zolai']}: {r.get('english_clean', r.get('english', ''))}")
        else:
            print("Dictionary: no results")

        # Bible
        bible_results = mgr.search_bible(text_val)
        if bible_results:
            print(f"\nBible ({len(bible_results)} results):")
            for r in bible_results[:5]:
                print(f"  [{r['ref']}] {r.get('zo_tdb77', '')[:80]}")

        # FTS5 combined search
        if mgr._fts5_available():
            fts_results = mgr.search_text(text_val)
            if fts_results:
                print(f"\nFTS5 ({len(fts_results)} results):")
                for r in fts_results[:5]:
                    table = r.get("table", "")
                    if table == "dictionary":
                        print(f"  [dict] {r.get('zolai', '')}: {r.get('english', '')}")
                    elif table == "bible_verses":
                        print(f"  [bible] {r.get('zo_tdb77', '')[:80]}")

    elif args.command == "stats":
        info = mgr.health_check()
        print(f"Backend: {info['backend']}")
        print(f"Size: {info['db_size_human']}")
        print(f"FTS5: {'available' if info['fts5_available'] else 'not available'}")
        print(f"Query latency: {info['query_latency_ms']}ms")
        print(f"\nTable counts:")
        for name, count in info["table_counts"].items():
            print(f"  {name}: {count:,}")
        print(f"\nTotal rows: {info['total_rows']:,}")

    mgr.dispose()


if __name__ == "__main__":
    main()
