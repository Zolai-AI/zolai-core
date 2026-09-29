"""Parity between the local alert evaluator and the deployed rule files.

There are three copies of the same thresholds:

1. ``zolai/monitoring/alerts.py`` — ``RULES`` used by ``/api/metrics/alerts``
2. ``ops/prometheus/rules.yml`` — Prometheus rule group
3. ``ops/grafana/provisioning/alerting/rules.yml`` — Grafana-managed rules

This test fails the build when any of them drifts, and checks that the local
evaluator actually fires on a synthetic bad window.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from zolai.monitoring.alerts import RULES, evaluate_alerts

REPO_ROOT = Path(__file__).resolve().parents[1]
PROM_RULES = REPO_ROOT / "ops" / "prometheus" / "rules.yml"
GRAFANA_RULES = (
    REPO_ROOT / "ops" / "grafana" / "provisioning" / "alerting" / "rules.yml"
)

def _alert_name(name: str) -> str:
    """``http_error_rate_high`` -> ``HttpErrorRateHigh`` (Prometheus name)."""
    return "".join(part.capitalize() for part in name.split("_"))

_TRAILING_NUMBER = re.compile(r"([0-9]*\.?[0-9]+)\s*$")
_DURATION = re.compile(r"^([0-9]*\.?[0-9]+)(ms|s|m|h|d)?$")

_UNIT_SCALE = {
    None: 1.0,
    "": 1.0,
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def _duration_seconds(value: Any) -> float:
    """Parse a Prometheus/Grafana duration (``5m``, ``30s``, ``0s``) to seconds."""
    match = _DURATION.match(str(value).strip())
    assert match, f"unparsable duration: {value!r}"
    return float(match.group(1)) * _UNIT_SCALE[match.group(2)]


def _threshold(expr: str) -> float:
    """The number an alert expression compares against (it must end with one)."""
    match = _TRAILING_NUMBER.search(expr.strip())
    assert match, f"expression must end with a threshold: {expr!r}"
    return float(match.group(1))


def _metric_tokens(expr: str) -> set[str]:
    return set(re.findall(r"zolai_[a-z0-9_]+", expr))


def _prom_alerts() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(PROM_RULES.read_text(encoding="utf-8"))
    rules = [rule for group in document["groups"] for rule in group["rules"]]
    return {rule["alert"]: rule for rule in rules}


def _grafana_rules() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(GRAFANA_RULES.read_text(encoding="utf-8"))
    rules = [rule for group in document["groups"] for rule in group["rules"]]
    # ``zolai-http-error-rate-high`` -> ``http_error_rate_high``
    return {
        rule["uid"].removeprefix("zolai-").replace("-", "_"): rule for rule in rules
    }


def _grafana_threshold(rule: dict[str, Any]) -> float:
    for query in rule["data"]:
        model = query.get("model", {})
        if model.get("type") == "threshold":
            return float(model["conditions"][0]["evaluator"]["params"][0])
    raise AssertionError(f"no threshold expression in Grafana rule {rule['uid']}")


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------
def test_prometheus_rules_match_local_evaluator() -> None:
    alerts = _prom_alerts()
    assert set(alerts) == {_alert_name(name) for name in RULES}

    for name, rule in RULES.items():
        alert = alerts[_alert_name(name)]
        assert _threshold(alert["expr"]) == pytest.approx(rule["threshold"]), name
        assert alert["labels"]["severity"] == rule["severity"], name
        assert _duration_seconds(alert["for"]) == pytest.approx(rule["for_s"]), name
        assert alert["annotations"]["summary"] == rule["summary"], name
        # Same metric families, so both sides observe the same series.
        assert _metric_tokens(rule["expr"]) <= _metric_tokens(alert["expr"]), name


def test_grafana_rules_match_local_evaluator() -> None:
    rules = _grafana_rules()
    assert set(rules) == set(RULES)

    for name, rule in RULES.items():
        provisioned = rules[name]
        assert _grafana_threshold(provisioned) == pytest.approx(rule["threshold"]), name
        assert provisioned["labels"]["severity"] == rule["severity"], name
        assert _duration_seconds(provisioned["for"]) == pytest.approx(rule["for_s"]), name
        assert provisioned["annotations"]["summary"] == rule["summary"], name
        assert provisioned["condition"] == "C"


# ---------------------------------------------------------------------------
# Evaluator behaviour
# ---------------------------------------------------------------------------
def test_evaluator_fires_on_bad_window() -> None:
    result = evaluate_alerts(
        {"error_rate": 0.5, "p95_s": 3.0, "db_query_p95_s": 0.25},
        window_s=60.0,
    )
    assert result["active_count"] == len(RULES)
    states = {rule["name"]: rule["state"] for rule in result["rules"]}
    assert set(states) == set(RULES)
    assert all(state == "firing" for state in states.values())


def test_evaluator_is_quiet_on_healthy_window() -> None:
    result = evaluate_alerts(
        {"error_rate": 0.0, "p95_s": 0.01, "db_query_p95_s": 0.005},
        window_s=60.0,
    )
    assert result["active_count"] == 0
    assert all(rule["state"] == "ok" for rule in result["rules"])
    assert result["evaluated_at"]
