"""Metrics REST API tests — schemas, annotations CRUD, health contract."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zolai.api.metrics_schemas import (
    AlertsResponse,
    EvalRunResponse,
    MetricsHealthResponse,
    MetricsInfoResponse,
    PerformanceResponse,
    SummaryResponse,
)
from zolai.api.server import create_app
from zolai.data.database import DatabaseManager
from zolai.monitoring import store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """App client with lifespan running (FK guard, build info, sampler)."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def temp_store(tmp_path, monkeypatch) -> Iterator[DatabaseManager]:
    """Isolate every annotation/eval/integrity read+write on a throwaway DB."""
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'monitoring.db'}")
    manager.init_db()
    monkeypatch.setattr(store, "_manager", lambda: manager)
    yield manager
    manager.dispose()


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------
def test_summary_endpoint_is_schema_valid(client: TestClient) -> None:
    response = client.get("/api/metrics/summary")
    assert response.status_code == 200
    payload = SummaryResponse.model_validate(response.json())
    assert payload.generated_at
    assert payload.db.size_bytes >= 0
    assert 0.0 <= payload.http.error_rate <= 1.0
    assert payload.business.dictionary_entries >= 0


def test_health_endpoint_is_schema_valid(
    client: TestClient, temp_store: DatabaseManager
) -> None:
    response = client.get("/api/metrics/health")
    assert response.status_code == 200
    payload = MetricsHealthResponse.model_validate(response.json())
    assert payload.status == "ok"
    assert payload.db.wal_mode
    assert payload.uptime_s >= 0


def test_health_returns_503_when_disk_unwritable(
    client: TestClient, temp_store: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        store,
        "disk_status",
        lambda path: {"path": str(path), "free_bytes": 0, "writable": False},
    )
    response = client.get("/api/metrics/health")
    assert response.status_code == 503
    payload = MetricsHealthResponse.model_validate(response.json())
    assert payload.status == "degraded"
    assert payload.disk.writable is False


def test_eval_endpoint_reports_none_without_runs(
    client: TestClient, temp_store: DatabaseManager
) -> None:
    response = client.get("/api/metrics/eval")
    assert response.status_code == 200
    payload = EvalRunResponse.model_validate(response.json())
    assert payload.source == "none"


def test_performance_endpoint_parses_window(client: TestClient) -> None:
    default = PerformanceResponse.model_validate(client.get("/api/metrics/performance").json())
    assert default.window_s == pytest.approx(300.0)

    quarter_hour = PerformanceResponse.model_validate(
        client.get("/api/metrics/performance?window=15m").json()
    )
    assert quarter_hour.window_s == pytest.approx(900.0)
    assert quarter_hour.samples >= 0


def test_alerts_endpoint_lists_every_rule(client: TestClient) -> None:
    response = client.get("/api/metrics/alerts")
    assert response.status_code == 200
    payload = AlertsResponse.model_validate(response.json())
    assert {rule.name for rule in payload.rules} == {
        "http_error_rate_high",
        "http_latency_p95_high",
        "db_query_p95_high",
    }
    assert payload.active_count == sum(1 for rule in payload.rules if rule.state == "firing")
    assert all(rule.state in {"ok", "firing"} for rule in payload.rules)


def test_info_endpoint_mirrors_build_info(client: TestClient) -> None:
    response = client.get("/api/metrics/info")
    assert response.status_code == 200
    payload = MetricsInfoResponse.model_validate(response.json())
    assert payload.version
    assert payload.metrics.enabled is True
    assert payload.metrics.rules_url == "/api/metrics/alerts"
    assert payload.db_backend == "sqlite"


def test_health_route_contract_is_preserved(client: TestClient) -> None:
    """``/health`` keeps its original keys — only ``uptime_s`` was added."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert isinstance(body["data_root"], str)
    assert isinstance(body["uptime_s"], float)


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
def test_annotations_crud_roundtrip(
    client: TestClient, temp_store: DatabaseManager
) -> None:
    created = client.post(
        "/api/metrics/annotations",
        json={
            "time": 1700000000,
            "title": "deploy",
            "text": "shipped metrics router",
            "tags": ["deploy", "v1"],
            "kind": "deploy",
        },
    )
    assert created.status_code == 201
    annotation_id = created.json()["id"]

    listed = client.get("/api/metrics/annotations")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert items[0]["id"] == annotation_id
    assert items[0]["time"] == 1700000000
    assert items[0]["tags"] == ["deploy", "v1"]
    assert items[0]["kind"] == "deploy"

    updated = client.put(
        f"/api/metrics/annotations/{annotation_id}", json={"title": "deploy-again"}
    )
    assert updated.status_code == 200
    assert updated.json() == {"updated": annotation_id}

    # Only the sent field changed; the rest kept their stored values.
    after = client.get("/api/metrics/annotations").json()[0]
    assert after["title"] == "deploy-again"
    assert after["text"] == "shipped metrics router"
    assert after["tags"] == ["deploy", "v1"]
    assert after["time"] == 1700000000

    deleted = client.delete(f"/api/metrics/annotations/{annotation_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": annotation_id}
    assert client.get("/api/metrics/annotations").json() == []


def test_annotation_pushes_grafana_body(
    client: TestClient, temp_store: DatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_push(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"id": 4242}

    monkeypatch.setattr(store, "push_annotation_to_grafana", fake_push)

    response = client.post(
        "/api/metrics/annotations",
        json={
            "title": "eval smoke",
            "text": "ok",
            "tags": ["eval"],
            "kind": "eval",
            "dashboardId": 7,
            "panelId": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["grafana_id"] == 4242

    assert captured["dashboardId"] == 7
    assert captured["panelId"] == 3
    assert captured["title"] == "eval smoke"
    assert captured["tags"] == ["eval"]
    assert isinstance(captured["time"], str)

    stored = client.get("/api/metrics/annotations").json()[0]
    assert stored["kind"] == "eval"


def test_annotation_missing_returns_404(
    client: TestClient, temp_store: DatabaseManager
) -> None:
    assert client.put("/api/metrics/annotations/4242", json={"title": "x"}).status_code == 404
    assert client.delete("/api/metrics/annotations/4242").status_code == 404


def test_annotation_rejects_unknown_kind(
    client: TestClient, temp_store: DatabaseManager
) -> None:
    response = client.post(
        "/api/metrics/annotations", json={"title": "x", "kind": "bogus"}
    )
    assert response.status_code == 422


def test_grafana_credentials_come_from_env_only() -> None:
    """The annotation path must never carry a credential in the payload."""
    import inspect

    source = inspect.getsource(store.push_annotation_to_grafana)
    assert "os.environ" in source
    assert "GRAFANA_URL" in source
    assert "GRAFANA_API_KEY" in source
