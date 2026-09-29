"""Prometheus exposition tests.

The default registry is **process-global and shared by the whole suite**, so
every assertion here compares a *delta* taken around an action — never an
absolute counter value.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import Sample, text_string_to_metric_families

from zolai.api.server import create_app
from zolai.monitoring import record_operation, track_operation
from zolai.monitoring.metrics import metric_value

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """App client with lifespan running (build info + sampler + FK guard)."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _samples(text: str) -> list[Sample]:
    """Flatten every sample in a Prometheus text exposition."""
    samples: list[Sample] = []
    for family in text_string_to_metric_families(text):
        samples.extend(family.samples)
    return samples


def _value(samples: list[Sample], name: str, **labels: str) -> float | None:
    """Value of the first sample matching ``name`` and all ``labels``."""
    for sample in samples:
        if sample.name != name:
            continue
        if all(sample.labels.get(key) == value for key, value in labels.items()):
            return sample.value
    return None


def _delta(client: TestClient, name: str, **labels: str) -> tuple[float, float]:
    """``(before, after)`` values of one sample around a single request."""
    before = _value(_samples(client.get("/metrics").text), name, **labels) or 0.0
    client.get("/health")
    after = _value(_samples(client.get("/metrics").text), name, **labels) or 0.0
    return before, after


def _probe() -> str:
    """Serialize the default registry directly (no HTTP round-trip)."""
    from prometheus_client import generate_latest

    return generate_latest().decode()


def _exercise_metric_families() -> None:
    """Touch every labelled family so it appears in the exposition.

    A labelled metric emits no samples until its first ``.labels()`` call, so
    families backed by rare paths (eval runs, analysis ops, DB errors) would
    otherwise be absent from ``/metrics``.
    """
    from zolai.data.database import DatabaseManager
    from zolai.monitoring.alerts import evaluate_alerts
    from zolai.monitoring.metrics import (
        EVAL_LAST_RUN,
        EVAL_METRIC_VALUE,
        EVAL_RUNS,
        WORDS_TRANSLATED,
    )

    with track_operation("family_probe_cm"):
        pass

    @record_operation("family_probe_dec")
    def _decorated_probe() -> None:
        return None

    _decorated_probe()
    WORDS_TRANSLATED.labels(direction="en-zo").inc()
    EVAL_RUNS.labels(set_name="family_probe").inc()
    EVAL_METRIC_VALUE.labels(set_name="family_probe", metric="accuracy").set(1.0)
    EVAL_LAST_RUN.set(time.time())
    evaluate_alerts()

    # Force one statement-level DB error so the error counter has a sample.
    with tempfile.TemporaryDirectory(prefix="zolai-metrics-") as tmp:
        manager = DatabaseManager(f"sqlite:///{pathlib.Path(tmp) / 'errors.db'}")
        try:
            with manager.engine.connect() as conn:
                conn.exec_driver_sql("SELECT * FROM no_such_table_for_metrics")
        except Exception:  # noqa: BLE001 — the failure is the point of the probe
            pass
        finally:
            manager.dispose()


# ---------------------------------------------------------------------------
# Exposition format
# ---------------------------------------------------------------------------
def test_metrics_exposition_is_prometheus_text_format(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]
    assert "# HELP" in response.text
    assert "# TYPE" in response.text


def test_exposition_contains_every_core_zolai_family(client: TestClient) -> None:
    _exercise_metric_families()
    names = {sample.name for sample in _samples(client.get("/metrics").text)}
    expected = [
        "zolai_http_requests_total",
        "zolai_http_request_duration_seconds_count",
        "zolai_http_request_duration_seconds_bucket",
        "zolai_http_requests_in_flight",
        "zolai_db_query_duration_seconds_count",
        "zolai_db_query_errors_total",
        "zolai_db_size_bytes",
        "zolai_db_wal_bytes",
        "zolai_db_integrity_status",
        "zolai_db_table_rows",
        "zolai_analysis_operations_total",
        "zolai_analysis_duration_seconds_count",
        "zolai_words_translated_total",
        "zolai_corpus_sentences",
        "zolai_dictionary_entries",
        "zolai_bible_verses",
        "zolai_eval_runs_total",
        "zolai_eval_last_run_timestamp_seconds",
        "zolai_alerts_active",
        "zolai_alert_state",
        "zolai_build_info",
    ]
    missing = [name for name in expected if name not in names]
    assert not missing, f"missing metric families: {missing}"


def test_exposition_keeps_process_and_python_defaults(client: TestClient) -> None:
    names = {sample.name for sample in _samples(client.get("/metrics").text)}
    assert any(name.startswith("process_") for name in names)
    assert any(name.startswith("python_") for name in names)


# ---------------------------------------------------------------------------
# Deltas around a request
# ---------------------------------------------------------------------------
def test_http_counter_increments_after_request(client: TestClient) -> None:
    before, after = _delta(client, "zolai_http_requests_total", method="GET", route="/health", status="200")
    assert after - before == pytest.approx(1.0)


def test_http_histogram_observes_request_duration(client: TestClient) -> None:
    before, after = _delta(client, "zolai_http_request_duration_seconds_count", method="GET", route="/health")
    assert after - before == pytest.approx(1.0)

    before_sum = _value(
        _samples(client.get("/metrics").text),
        "zolai_http_request_duration_seconds_sum",
        method="GET",
        route="/health",
    )
    client.get("/health")
    after_sum = _value(
        _samples(client.get("/metrics").text),
        "zolai_http_request_duration_seconds_sum",
        method="GET",
        route="/health",
    )
    assert before_sum is not None and after_sum is not None
    assert after_sum > before_sum


def test_in_flight_gauge_returns_to_zero(client: TestClient) -> None:
    from zolai.monitoring.metrics import HTTP_IN_FLIGHT

    client.get("/health")
    # Read the gauge object: the exposition request itself is still in flight
    # while ``/metrics`` is being generated, so the wire value is never 0.
    assert metric_value(HTTP_IN_FLIGHT) == pytest.approx(0.0)


def test_route_labels_are_templates_never_raw_paths(client: TestClient) -> None:
    raw_path = "/api/metrics/annotations/987654321"
    client.put(raw_path, json={"title": "probe"})

    samples = _samples(client.get("/metrics").text)
    routes = {
        sample.labels.get("route")
        for sample in samples
        if sample.name == "zolai_http_requests_total"
    }
    assert raw_path not in routes
    assert any(route and "{" in route for route in routes), routes


# ---------------------------------------------------------------------------
# Build info + analysis instrumentation
# ---------------------------------------------------------------------------
def test_build_info_metric_matches_info_endpoint(client: TestClient) -> None:
    from zolai.monitoring.store import build_info

    info = build_info()
    samples = [
        sample
        for sample in _samples(client.get("/metrics").text)
        if sample.name == "zolai_build_info"
    ]
    assert len(samples) == 1
    assert samples[0].value == pytest.approx(1.0)
    assert samples[0].labels["version"] == info["version"]
    assert samples[0].labels["commit"] == info["commit"]


def test_track_operation_context_counts_and_times() -> None:
    before_ops = _value(
        _samples(_probe()), "zolai_analysis_operations_total", operation="unit_probe_cm"
    )
    with track_operation("unit_probe_cm"):
        pass
    after_ops = _value(
        _samples(_probe()), "zolai_analysis_operations_total", operation="unit_probe_cm"
    )
    assert (after_ops or 0.0) - (before_ops or 0.0) == pytest.approx(1.0)


def test_record_operation_decorator_counts_and_times() -> None:
    @record_operation("unit_probe_dec")
    def probe() -> str:
        return "ok"

    before = _value(
        _samples(_probe()), "zolai_analysis_operations_total", operation="unit_probe_dec"
    )
    assert probe() == "ok"
    after = _value(
        _samples(_probe()), "zolai_analysis_operations_total", operation="unit_probe_dec"
    )
    assert (after or 0.0) - (before or 0.0) == pytest.approx(1.0)

    histogram = _value(
        _samples(_probe()),
        "zolai_analysis_duration_seconds_count",
        operation="unit_probe_dec",
    )
    assert histogram is not None and histogram >= 1.0
