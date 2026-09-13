"""
Database Abstraction Layer for Zolai.

Provides a unified interface supporting both SQLite (local) and PostgreSQL (production).
Uses SQLAlchemy 2.0 with async support for future-proofing.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from sqlalchemy import (
    Column, DateTime, Integer, String, Text, create_engine, event, inspect, text
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, declared_attr
from sqlalchemy.pool import NullPool


# ─── Configuration ────────────────────────────────────────────────────────

class DatabaseConfig:
    """Database configuration from environment."""
    
    def __init__(self):
        self.sqlite_path = os.getenv("ZOLAI_SQLITE_PATH", "/home/peter/Documents/Projects/zolai-ai/data/zolai.db")
        self.postgres_url = os.getenv("ZOLAI_POSTGRES_URL", "")
        self.postgres_async_url = os.getenv("ZOLAI_POSTGRES_ASYNC_URL", "")
        self.use_postgres = bool(self.postgres_url)
        self.echo = os.getenv("ZOLAI_DB_ECHO", "false").lower() == "true"
        self.pool_size = int(os.getenv("ZOLAI_DB_POOL_SIZE", "5"))
        self.max_overflow = int(os.getenv("ZOLAI_DB_MAX_OVERFLOW", "10"))
    
    @property
    def sync_url(self) -> str:
        if self.use_postgres:
            return self.postgres_url
        return f"sqlite:///{self.sqlite_path}"
    
    @property
    def async_url(self) -> str:
        if self.use_postgres:
            return self.postgres_async_url or self.postgres_url.replace("postgresql://", "postgresql+asyncpg://")
        return f"sqlite+aiosqlite:///{self.sqlite_path}"


config = DatabaseConfig()


# ─── SQLAlchemy Base ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Base class for all models with common columns."""
    
    @declared_attr.directive
    def __tablename__(cls) -> str:
        return cls.__name__.lower()
    
    # Common columns for all models
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Version tracking (for JSONL imports)
    import_batch_id = Column(String(36), nullable=True, index=True)
    source_file = Column(String(500), nullable=True)
    version = Column(Integer, nullable=True, default=1)
    imported_at = Column(DateTime(timezone=True), nullable=True)
    
    # Soft delete
    is_deleted = Column(Integer, default=0, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(100), nullable=True)


# ─── Engine Management ────────────────────────────────────────────────────

class DatabaseManager:
    """Manages database connections for both sync and async operations."""
    
    def __init__(self, config: DatabaseConfig = config):
        self.config = config
        self._sync_engine: Optional[Engine] = None
        self._async_engine: Optional[AsyncEngine] = None
        self._sync_session_factory: Optional[sessionmaker] = None
        self._async_session_factory: Optional[sessionmaker] = None
    
    def get_sync_engine(self) -> Engine:
        if self._sync_engine is None:
            if config.use_postgres:
                self._sync_engine = create_engine(
                    config.sync_url,
                    echo=config.echo,
                    pool_size=config.pool_size,
                    max_overflow=config.max_overflow,
                    pool_pre_ping=True,
                )
            else:
                self._sync_engine = create_engine(
                    config.sync_url,
                    echo=config.echo,
                    connect_args={"check_same_thread": False, "timeout": 30},
                    poolclass=NullPool,
                )
                # Enable WAL mode for SQLite
                @event.listens_for(self._sync_engine, "connect")
                def set_sqlite_pragma(dbapi_connection, connection_record):
                    cursor = dbapi_connection.cursor()
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA busy_timeout=30000")
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()
        return self._sync_engine
    
    def get_async_engine(self) -> AsyncEngine:
        if self._async_engine is None:
            self._async_engine = create_async_engine(
                config.async_url,
                echo=config.echo,
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_pre_ping=True,
            )
        return self._async_engine
    
    def get_sync_session(self) -> Session:
        if self._sync_session_factory is None:
            self._sync_session_factory = sessionmaker(bind=self.get_sync_engine(), expire_on_commit=False)
        return self._sync_session_factory()
    
    @asynccontextmanager
    async def get_async_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._async_session_factory is None:
            self._async_session_factory = sessionmaker(
                bind=self.get_async_engine(), class_=AsyncSession, expire_on_commit=False
            )
        async with self._async_session_factory() as session:
            yield session
    
    def create_all_tables(self):
        """Create all tables defined in models."""
        Base.metadata.create_all(self.get_sync_engine())
    
    async def create_all_tables_async(self):
        """Create all tables asynchronously."""
        async with self.get_async_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    def drop_all_tables(self):
        Base.metadata.drop_all(self.get_sync_engine())
    
    def get_table_info(self) -> dict:
        """Get information about all tables."""
        inspector = inspect(self.get_sync_engine())
        tables = {}
        for table_name in inspector.get_table_names():
            columns = inspector.get_columns(table_name)
            tables[table_name] = {
                "columns": [c["name"] for c in columns],
                "row_count": self._count_rows(table_name)
            }
        return tables
    
    def _count_rows(self, table_name: str) -> int:
        with self.get_sync_session() as session:
            return session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar()
    
    def close(self):
        if self._sync_engine:
            self._sync_engine.dispose()
        if self._async_engine:
            import asyncio
            asyncio.run(self._async_engine.dispose())


# ─── Singleton Instance ───────────────────────────────────────────────────

_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """Get singleton database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_session() -> Session:
    """Get a synchronous database session."""
    return get_db_manager().get_sync_session()


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an asynchronous database session."""
    async with get_db_manager().get_async_session() as session:
        yield session


# ─── Migration Helpers ────────────────────────────────────────────────────

def migrate_sqlite_to_postgres(sqlite_path: str, postgres_url: str):
    """Migrate data from SQLite to PostgreSQL."""
    # This is a placeholder for the actual migration logic
    # Would use pg_dump/pg_restore or custom ETL
    raise NotImplementedError("Use pg_dump/pg_restore or custom ETL for migration")


def init_database(use_postgres: bool = False, postgres_url: str = ""):
    """Initialize database with proper configuration."""
    if use_postgres and postgres_url:
        os.environ["ZOLAI_POSTGRES_URL"] = postgres_url
        os.environ["ZOLAI_USE_POSTGRES"] = "true"
    elif use_postgres:
        raise ValueError("PostgreSQL URL required when use_postgres=True")
    
    # Reinitialize config
    global config
    config = DatabaseConfig()
    
    # Create tables
    manager = get_db_manager()
    manager.create_all_tables()
    
    return manager
