"""
Metrics Dashboard - Collects, stores, and visualizes learning metrics.

All metric samples live in the project SQLite database (``learning_metrics``);
nothing is written to a file.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...data.repositories import get_engine

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """A single metric snapshot."""

    timestamp: datetime
    metric_name: str
    value: float
    labels: dict = field(default_factory=dict)
    source: str = "system"


class MetricCollector:
    """Collects and stores metrics in the project database."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create the metrics table if it does not exist (additive, idempotent)."""
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS learning_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    labels TEXT DEFAULT '{}',
                    source TEXT DEFAULT 'system'
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_learning_metrics_name_time
                ON learning_metrics(metric_name, timestamp)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_learning_metrics_timestamp
                ON learning_metrics(timestamp)
            """))

    def record_metric(
        self,
        name: str,
        value: float,
        labels: dict = None,
        source: str = "system",
        timestamp: datetime = None,
    ):
        """Record a metric value."""
        ts = timestamp or datetime.utcnow()

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO learning_metrics (timestamp, metric_name, value, labels, source)
                    VALUES (:timestamp, :name, :value, :labels, :source)
                """),
                {
                    "timestamp": ts.isoformat(),
                    "name": name,
                    "value": float(value),
                    "labels": json.dumps(labels or {}),
                    "source": source,
                },
            )

    def record_batch(self, metrics: list[dict]):
        """Record multiple metrics at once."""
        now = datetime.utcnow().isoformat()
        rows = [
            {
                "timestamp": m.get("timestamp") or now,
                "name": m["name"],
                "value": float(m["value"]),
                "labels": json.dumps(m.get("labels", {})),
                "source": m.get("source", "system"),
            }
            for m in metrics
        ]
        if not rows:
            return

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO learning_metrics (timestamp, metric_name, value, labels, source)
                    VALUES (:timestamp, :name, :value, :labels, :source)
                """),
                rows,
            )

    def query_metrics(
        self,
        name: str,
        since: datetime = None,
        until: datetime = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Query metrics from the database."""
        clauses = ["metric_name = :name"]
        params: dict = {"name": name}

        if since:
            clauses.append("timestamp >= :since")
            params["since"] = since.isoformat()
        if until:
            clauses.append("timestamp <= :until")
            params["until"] = until.isoformat()
        params["limit"] = limit

        sql = text(f"""
            SELECT timestamp, metric_name, value, labels, source
            FROM learning_metrics
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp DESC, id DESC
            LIMIT :limit
        """)

        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        samples = []
        for row in rows:
            sample = dict(row._mapping)
            try:
                sample["labels"] = json.loads(sample.get("labels") or "{}")
            except (TypeError, ValueError):
                sample["labels"] = {}
            samples.append(sample)
        return samples

    def get_latest(self, name: str) -> dict | None:
        """Get latest value for a metric."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT timestamp, metric_name, value, labels, source
                    FROM learning_metrics
                    WHERE metric_name = :name
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 1
                """),
                {"name": name},
            ).fetchone()
            return dict(row._mapping) if row else None

    def get_metric_stats(self, name: str, since: datetime = None) -> dict:
        """Get statistics for a metric."""
        since = since or datetime.utcnow() - timedelta(days=7)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT value FROM learning_metrics
                    WHERE metric_name = :name AND timestamp >= :since
                    ORDER BY timestamp
                """),
                {"name": name, "since": since.isoformat()},
            ).fetchall()
            values = [row[0] for row in rows]

        if not values:
            return {"count": 0}

        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
            "latest": values[-1],
        }

    def get_all_metric_names(self) -> list[str]:
        """Get all metric names."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT metric_name FROM learning_metrics ORDER BY metric_name")
            ).fetchall()
            return [row[0] for row in rows]


class MetricsDashboard:
    """Main dashboard for visualizing learning metrics."""

    def __init__(self, engine: Engine = None):
        self.engine = engine or get_engine()
        self.collector = MetricCollector(engine=self.engine)
        self._collectors: list[Callable] = []

    def register_collector(self, collector: Callable):
        """Register a custom metric collector function."""
        self._collectors.append(collector)

    async def collect_all_metrics(self) -> dict:
        """Collect all metrics from registered collectors and system."""
        metrics = {}

        # System metrics
        metrics["system"] = await self._collect_system_metrics()

        # Agent metrics
        metrics["agents"] = await self._collect_agent_metrics()

        # Learning metrics
        metrics["learning"] = await self._collect_learning_metrics()

        # Feedback metrics
        metrics["feedback"] = await self._collect_feedback_metrics()

        # Custom collectors
        for collector in self._collectors:
            try:
                extra = collector()
                if hasattr(extra, "__await__"):
                    extra = await extra
                if isinstance(extra, dict):
                    metrics.setdefault("custom", {}).update(extra)
            except Exception as e:
                logger.warning(f"Custom collector failed: {e}")

        # Store all metrics
        for category, category_metrics in metrics.items():
            for name, value in category_metrics.items():
                self.collector.record_metric(
                    f"{category}.{name}",
                    value,
                    labels={"category": category},
                    source="dashboard",
                )

        return metrics

    async def _collect_system_metrics(self) -> dict:
        """Collect system-level metrics."""
        try:
            import psutil

            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            return {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": memory.available / (1024**3),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free / (1024**3),
                "process_count": len(psutil.pids()),
            }
        except Exception as e:
            logger.warning(f"Failed to collect system metrics: {e}")
            return {}

    async def _collect_agent_metrics(self) -> dict:
        """Collect metrics from all agents."""
        queries = {
            "total": "SELECT COUNT(*) FROM agents",
            "enabled": "SELECT COUNT(*) FROM agents WHERE enabled = 1",
            "skills": "SELECT COUNT(*) FROM agent_skills",
        }
        stats: dict = {}
        try:
            with self.engine.connect() as conn:
                for name, sql in queries.items():
                    try:
                        stats[name] = conn.execute(text(sql)).scalar() or 0
                    except Exception:
                        # Table absent in a trimmed DB — skip rather than fail.
                        continue
        except Exception as e:
            logger.warning(f"Failed to collect agent metrics: {e}")
            return {}
        return stats

    async def _collect_learning_metrics(self) -> dict:
        """Collect learning progress metrics."""
        return await self._collect_learning_progress()

    async def _collect_learning_progress(self) -> dict:
        """Count rows in the core learning tables."""
        queries = {
            "vocabulary_size": "SELECT COUNT(*) FROM vocabulary",
            "patterns_learned": "SELECT COUNT(*) FROM grammar_patterns",
            "collocations": "SELECT COUNT(*) FROM word_collocations",
            "bible_verses": "SELECT COUNT(*) FROM bible_verses",
        }
        stats: dict = {}
        try:
            with self.engine.connect() as conn:
                for name, sql in queries.items():
                    try:
                        stats[name] = conn.execute(text(sql)).scalar() or 0
                    except Exception:
                        # Table absent in a trimmed DB — skip rather than fail.
                        continue
        except Exception as e:
            logger.warning(f"Failed to collect learning metrics: {e}")
            return {}
        return stats

    async def _collect_feedback_metrics(self) -> dict:
        """Collect feedback-related metrics."""
        try:
            from ..feedback_store import FeedbackStore

            store = FeedbackStore(engine=self.engine)
            pending = store.get_pending_review(limit=1000)

            return {
                "total_pending": len(pending),
                "positive": sum(1 for s in pending if s.get("feedback_type") == "explicit_up"),
                "negative": sum(1 for s in pending if s.get("feedback_type") == "explicit_down"),
                "corrections": sum(1 for s in pending if s.get("feedback_type") == "correction"),
            }
        except Exception as e:
            logger.warning(f"Failed to collect feedback metrics: {e}")
            return {}

    def get_dashboard_data(self, hours: int = 24) -> dict:
        """Get all dashboard data for the last N hours."""
        since = datetime.utcnow() - timedelta(hours=hours)

        data = {
            "timestamp": datetime.utcnow().isoformat(),
            "window_hours": hours,
            "metrics": {},
        }

        for name in self.collector.get_all_metric_names():
            stats = self.collector.get_metric_stats(name, since)
            if stats["count"] > 0:
                data["metrics"][name] = stats

        return data

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []
        since = datetime.utcnow() - timedelta(hours=24)

        for name in self.collector.get_all_metric_names():
            stats = self.collector.get_metric_stats(name, since)
            if stats["count"] == 0:
                continue

            # Sanitize metric name for Prometheus
            safe_name = name.replace(".", "_").replace("-", "_")

            lines.append(f"# HELP {safe_name} Metric: {name}")
            lines.append(f"# TYPE {safe_name} gauge")
            lines.append(f"{safe_name} {stats['latest']}")
            lines.append(f"{safe_name}_min {stats['min']}")
            lines.append(f"{safe_name}_max {stats['max']}")
            lines.append(f"{safe_name}_mean {stats['mean']}")
            lines.append(f"{safe_name}_count {stats['count']}")

        return "\n".join(lines)

    def export_json(self, hours: int = 24) -> str:
        """Render dashboard data as a JSON string."""
        data = self.get_dashboard_data(hours)
        return json.dumps(data, indent=2)

    async def start_periodic_collection(self, interval: int = 60, iterations: int | None = None):
        """Start periodic metric collection.

        ``iterations`` bounds the loop (used by tests); ``None`` runs forever.
        """
        count = 0
        while iterations is None or count < iterations:
            try:
                await self.collect_all_metrics()
            except Exception as e:
                logger.error(f"Metric collection error: {e}")
            count += 1
            if iterations is not None and count >= iterations:
                break
            await asyncio.sleep(interval)


# Convenience functions
def create_dashboard(engine: Engine = None) -> MetricsDashboard:
    """Create a metrics dashboard with database engine."""
    return MetricsDashboard(engine)


def create_collector(engine: Engine = None) -> MetricCollector:
    """Create a metric collector backed by the project database."""
    return MetricCollector(engine=engine)
