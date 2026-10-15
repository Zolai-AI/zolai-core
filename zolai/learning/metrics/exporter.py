"""
Metrics Exporter - Renders metrics in various wire formats.

Every exporter returns a string (or a dict) — nothing is written to a file.
Callers that want to persist an export hand the string to their own transport.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from .dashboard import MetricsDashboard

logger = logging.getLogger(__name__)


class MetricsExporter:
    """Renders metrics in various formats and destinations."""

    def __init__(self, dashboard: MetricsDashboard):
        self.dashboard = dashboard

    def export_json(self, hours: int = 24) -> str:
        """Render metrics as a JSON string."""
        return json.dumps(self.dashboard.get_dashboard_data(hours=hours), indent=2, default=str)

    def export_prometheus(self) -> str:
        """Render metrics in Prometheus text exposition format."""
        return self.dashboard.export_prometheus()

    def export_csv(self, hours: int = 24) -> str:
        """Render metrics as CSV."""
        since = datetime.utcnow() - timedelta(hours=hours)

        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow(["metric_name", "timestamp", "value", "labels", "source"])

        for name in self.dashboard.collector.get_all_metric_names():
            metrics = self.dashboard.collector.query_metrics(name, since=since)
            for m in metrics:
                writer.writerow([
                    m["metric_name"],
                    m["timestamp"],
                    m["value"],
                    m["labels"],
                    m["source"],
                ])

        return output.getvalue()

    def export_influxdb_line(self, hours: int = 24) -> str:
        """Render metrics in InfluxDB line protocol format."""
        since = datetime.utcnow() - timedelta(hours=hours)

        lines = []
        for name in self.dashboard.collector.get_all_metric_names():
            metrics = self.dashboard.collector.query_metrics(name, since=since)
            for m in metrics:
                safe_name = name.replace(".", "_").replace("-", "_")
                labels = m.get("labels", {})
                label_str = ",".join(f'{k}="{v}"' for k, v in labels.items()) if labels else ""
                if label_str:
                    label_str = f",{label_str}"
                nanos = int(datetime.fromisoformat(m["timestamp"]).timestamp() * 1e9)
                lines.append(f"{safe_name}{label_str} value={m['value']} {nanos}")

        return "\n".join(lines)

    def export_grafana_dashboard(self) -> dict:
        """Build a Grafana dashboard definition (as a dict, not a file)."""
        return {
            "dashboard": {
                "title": "Zolai Learning System Metrics",
                "tags": ["zolai", "learning", "agents"],
                "timezone": "utc",
                "panels": [
                    {
                        "title": "System CPU Usage",
                        "type": "graph",
                        "targets": [{"expr": "system_cpu_percent", "legendFormat": "CPU %"}],
                        "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
                    },
                    {
                        "title": "Memory Usage",
                        "type": "graph",
                        "targets": [{"expr": "system_memory_percent", "legendFormat": "Memory %"}],
                        "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
                    },
                    {
                        "title": "Learning Tasks Completed",
                        "type": "stat",
                        "targets": [
                            {"expr": "learning_tasks_completed_total", "legendFormat": "Completed"},
                        ],
                        "gridPos": {"x": 0, "y": 8, "w": 6, "h": 4},
                    },
                    {
                        "title": "Feedback Signals",
                        "type": "graph",
                        "targets": [
                            {"expr": "feedback_positive", "legendFormat": "Positive"},
                            {"expr": "feedback_negative", "legendFormat": "Negative"},
                            {"expr": "feedback_corrections", "legendFormat": "Corrections"},
                        ],
                        "gridPos": {"x": 6, "y": 8, "w": 12, "h": 8},
                    },
                    {
                        "title": "Agent Performance",
                        "type": "table",
                        "targets": [{"expr": "agents_accuracy", "legendFormat": "{{agent_id}}"}],
                        "gridPos": {"x": 0, "y": 12, "w": 24, "h": 10},
                    },
                    {
                        "title": "Learning Progress",
                        "type": "graph",
                        "targets": [
                            {"expr": "learning_vocabulary_size", "legendFormat": "Vocabulary"},
                            {"expr": "learning_patterns_learned", "legendFormat": "Patterns"},
                            {"expr": "learning_collocations", "legendFormat": "Collocations"},
                        ],
                        "gridPos": {"x": 0, "y": 22, "w": 24, "h": 8},
                    },
                ],
                "time": {"from": "now-24h", "to": "now"},
                "refresh": "30s",
            },
        }

    def export_to_influxdb(
        self,
        hours: int = 24,
        url: str = "http://localhost:8086",
        token: str = "",
        org: str = "zolai",
        bucket: str = "metrics",
    ) -> bool:
        """Push metrics to InfluxDB (skipped cleanly when the client is absent)."""
        try:
            from influxdb_client import InfluxDBClient, Point
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError:
            logger.warning("influxdb-client not installed — skipping InfluxDB export")
            return False

        try:
            client = InfluxDBClient(url=url, token=token, org=org)
            write_api = client.write_api(write_options=SYNCHRONOUS)

            since = datetime.utcnow() - timedelta(hours=hours)
            for name in self.dashboard.collector.get_all_metric_names():
                metrics = self.dashboard.collector.query_metrics(name, since=since)
                for m in metrics:
                    point = (
                        Point("zolai_metric")
                        .tag("metric", name)
                        .tag("source", m.get("source", "system"))
                        .field("value", m["value"])
                        .time(datetime.fromisoformat(m["timestamp"]))
                    )
                    write_api.write(bucket=bucket, org=org, record=point)

            logger.info(f"Exported metrics to InfluxDB bucket {bucket}")
            return True
        except Exception as e:
            logger.error(f"Failed to export to InfluxDB: {e}")
            return False

    def schedule_exports(
        self,
        on_export: Callable[[str, str], None],
        snapshot_interval: int = 3600,
        prometheus_interval: int = 60,
    ) -> threading.Thread:
        """Run periodic in-process exports on a daemon thread.

        ``on_export(kind, payload)`` receives every rendered payload; the
        exporter itself never touches the filesystem. Returns the started thread
        so callers can join or stop it.
        """
        stop = threading.Event()

        def loop() -> None:
            last_snapshot = 0.0
            last_prometheus = 0.0
            while not stop.is_set():
                now = time.monotonic()
                try:
                    if now - last_prometheus >= prometheus_interval:
                        on_export("prometheus", self.export_prometheus())
                        last_prometheus = now
                    if now - last_snapshot >= snapshot_interval:
                        on_export("json", self.export_json())
                        on_export("csv", self.export_csv())
                        last_snapshot = now
                except Exception as e:
                    logger.error(f"Scheduled export failed: {e}")
                stop.wait(1.0)

        thread = threading.Thread(target=loop, daemon=True, name="zolai-metrics-exporter")
        thread.stop = stop  # type: ignore[attr-defined]
        thread.start()
        logger.info("Scheduled periodic metrics exports")
        return thread


# Convenience functions
def create_exporter(dashboard: MetricsDashboard) -> MetricsExporter:
    """Create a metrics exporter."""
    return MetricsExporter(dashboard)


def export_all_formats(dashboard: MetricsDashboard) -> dict[str, str]:
    """Render metrics in all supported formats (no files written)."""
    exporter = MetricsExporter(dashboard)
    return {
        "json": exporter.export_json(),
        "prometheus": exporter.export_prometheus(),
        "csv": exporter.export_csv(),
        "influxdb": exporter.export_influxdb_line(),
        "grafana": json.dumps(exporter.export_grafana_dashboard(), indent=2),
    }
