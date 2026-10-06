"""Tests for circuit breaker implementation."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from zolai.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    circuit_breaker,
    circuit_breaker_context,
    get_circuit_breaker,
)


def test_circuit_breaker_initial_state():
    """Test circuit breaker starts in CLOSED state."""
    cb = CircuitBreaker("test_initial", CircuitBreakerConfig(enabled=True))
    stats = cb.get_stats()
    assert stats["state"] == "closed"
    assert stats["failures"] == 0
    assert stats["successes"] == 0


def test_circuit_breaker_success():
    """Test successful call increments success counter."""
    cb = CircuitBreaker("test_success", CircuitBreakerConfig(enabled=True))

    def success_func():
        return "ok"

    result = cb.call(success_func)
    assert result == "ok"

    stats = cb.get_stats()
    assert stats["successes"] == 1
    assert stats["total_calls"] == 1
    assert stats["state"] == "closed"


def test_circuit_breaker_failure_threshold():
    """Test circuit opens after failure threshold reached."""
    config = CircuitBreakerConfig(failure_threshold=3, enabled=True)
    cb = CircuitBreaker("test_threshold", config)

    def fail_func():
        raise ValueError("fail")

    # First 2 failures - should stay closed
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    stats = cb.get_stats()
    assert stats["failures"] == 2
    assert stats["state"] == "closed"

    # 3rd failure - should open
    with pytest.raises(ValueError):
        cb.call(fail_func)

    stats = cb.get_stats()
    assert stats["failures"] == 3
    assert stats["state"] == "open"


def test_circuit_breaker_open_rejects_calls():
    """Test OPEN circuit rejects calls immediately."""
    config = CircuitBreakerConfig(failure_threshold=2, enabled=True)
    cb = CircuitBreaker("test_open_reject", config)

    def fail_func():
        raise ValueError("fail")

    # Trigger OPEN state
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    # Next call should be rejected by circuit breaker
    with pytest.raises(CircuitBreakerError):
        cb.call(lambda: "ok")

    stats = cb.get_stats()
    assert stats["rejected_calls"] == 1
    assert stats["state"] == "open"


def test_circuit_breaker_half_open_after_timeout():
    """Test circuit transitions to HALF_OPEN after timeout."""
    config = CircuitBreakerConfig(failure_threshold=2, timeout=0.1, enabled=True)
    cb = CircuitBreaker("test_half_open", config)

    def fail_func():
        raise ValueError("fail")

    # Trigger OPEN state
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    assert cb.get_stats()["state"] == "open"

    # Wait for timeout
    time.sleep(0.15)

    # Next call should attempt (HALF_OPEN)
    with pytest.raises(ValueError):
        cb.call(fail_func)

    stats = cb.get_stats()
    assert stats["state"] == "open"  # Failed in HALF_OPEN -> back to OPEN


def test_circuit_breaker_half_open_success_closes():
    """Test successful calls in HALF_OPEN close the circuit."""
    config = CircuitBreakerConfig(
        failure_threshold=2,
        timeout=0.1,
        success_threshold=2,
        enabled=True,
    )
    cb = CircuitBreaker("test_half_open_success", config)

    def fail_func():
        raise ValueError("fail")

    def success_func():
        return "ok"

    # Trigger OPEN state
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    # Wait for timeout
    time.sleep(0.15)

    # Two successes in HALF_OPEN should close
    result = cb.call(success_func)
    assert result == "ok"
    assert cb.get_stats()["state"] == "half_open"

    result = cb.call(success_func)
    assert result == "ok"
    assert cb.get_stats()["state"] == "closed"


def test_circuit_breaker_context_manager():
    """Test circuit breaker context manager."""
    cb = CircuitBreaker("test_context", CircuitBreakerConfig(enabled=True))

    with cb.protect():
        result = "ok"

    assert result == "ok"
    assert cb.get_stats()["successes"] == 1

    # Test failure in context
    with pytest.raises(ValueError):
        with cb.protect():
            raise ValueError("fail")

    assert cb.get_stats()["failures"] == 1


def test_circuit_breaker_decorator_sync():
    """Test circuit breaker decorator on sync function."""
    config = CircuitBreakerConfig(failure_threshold=2, enabled=True)

    @circuit_breaker("test_decorator_sync", config)
    def my_func(x: int) -> int:
        if x < 0:
            raise ValueError("negative")
        return x * 2

    assert my_func(5) == 10

    with pytest.raises(ValueError):
        my_func(-1)

    with pytest.raises(ValueError):
        my_func(-1)

    # Third call should be rejected
    with pytest.raises(CircuitBreakerError):
        my_func(5)


@pytest.mark.asyncio
async def test_circuit_breaker_decorator_async():
    """Test circuit breaker decorator on async function."""
    config = CircuitBreakerConfig(failure_threshold=2, enabled=True)

    @circuit_breaker("test_decorator_async", config)
    async def my_async_func(x: int) -> int:
        if x < 0:
            raise ValueError("negative")
        return x * 2

    assert await my_async_func(5) == 10

    with pytest.raises(ValueError):
        await my_async_func(-1)

    with pytest.raises(ValueError):
        await my_async_func(-1)

    # Third call should be rejected
    with pytest.raises(CircuitBreakerError):
        await my_async_func(5)


def test_circuit_breaker_context_manager_function():
    """Test circuit_breaker_context function."""
    config = CircuitBreakerConfig(failure_threshold=2, enabled=True)

    with circuit_breaker_context("test_context_func", config):
        pass

    cb = get_circuit_breaker("test_context_func", config)
    assert cb.get_stats()["successes"] == 1

    with pytest.raises(ValueError):
        with circuit_breaker_context("test_context_func", config):
            raise ValueError("fail")

    assert cb.get_stats()["failures"] == 1


def test_circuit_breaker_reset():
    """Test manual reset of circuit breaker."""
    config = CircuitBreakerConfig(failure_threshold=2, enabled=True)
    cb = CircuitBreaker("test_reset", config)

    def fail_func():
        raise ValueError("fail")

    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    assert cb.get_stats()["state"] == "open"

    cb.reset()
    stats = cb.get_stats()
    assert stats["state"] == "closed"
    assert stats["failures"] == 0
    assert stats["successes"] == 0


def test_circuit_breaker_force_open():
    """Test manual force open of circuit breaker."""
    cb = CircuitBreaker("test_force_open", CircuitBreakerConfig(enabled=True))
    assert cb.get_stats()["state"] == "closed"

    cb.force_open()
    assert cb.get_stats()["state"] == "open"

    with pytest.raises(CircuitBreakerError):
        cb.call(lambda: "ok")


def test_circuit_breaker_disabled():
    """Test circuit breaker when disabled via config."""
    config = CircuitBreakerConfig(enabled=False)
    cb = CircuitBreaker("test_disabled", config)

    def fail_func():
        raise ValueError("fail")

    # Should not track failures when disabled - calls execute but no tracking
    for _ in range(10):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    stats = cb.get_stats()
    assert stats["state"] == "closed"
    assert stats["failures"] == 0
    # When disabled, no tracking occurs
    assert stats["total_calls"] == 0


def test_circuit_breaker_singleton():
    """Test get_circuit_breaker returns singleton."""
    cb1 = get_circuit_breaker("test_singleton")
    cb2 = get_circuit_breaker("test_singleton")
    assert cb1 is cb2


def test_circuit_breaker_config_from_env():
    """Test config loading from environment."""
    with patch.dict(
        os.environ,
        {
            "ZOLAI_CB_FAILURE_THRESHOLD": "10",
            "ZOLAI_CB_TIMEOUT": "60",
            "ZOLAI_CB_SUCCESS_THRESHOLD": "3",
            "ZOLAI_CB_ENABLED": "false",
        },
    ):
        config = CircuitBreakerConfig.from_env()
        assert config.failure_threshold == 10
        assert config.timeout == 60.0
        assert config.success_threshold == 3
        assert config.enabled is False


def test_circuit_breaker_metrics():
    """Test Prometheus metrics are updated."""
    # Use unique name to avoid test pollution
    import uuid

    from prometheus_client import REGISTRY
    unique_name = f"test_metrics_{uuid.uuid4().hex[:8]}"
    cb = CircuitBreaker(unique_name, CircuitBreakerConfig(enabled=True))

    # Initial state gauge - filter by name
    gauge_metric = next(m for m in REGISTRY.collect() if m.name == "circuit_breaker_state")
    gauge_value = next(s for s in gauge_metric.samples if s.labels.get("name") == unique_name).value
    assert gauge_value == 0.0  # closed

    def fail_func():
        raise ValueError("fail")

    # Trigger OPEN
    for _ in range(5):
        with pytest.raises(ValueError):
            cb.call(fail_func)

    # Check state gauge updated - filter by our circuit breaker name
    gauge_metric = next(m for m in REGISTRY.collect() if m.name == "circuit_breaker_state")
    gauge_value = next(s for s in gauge_metric.samples if s.labels.get("name") == unique_name).value
    assert gauge_value == 1.0  # open

    # Check counters incremented - filter by our name (metric name is 'circuit_breaker_failures')
    failure_counter = next(
        m for m in REGISTRY.collect() if m.name == "circuit_breaker_failures"
    )
    failure_value = next(
        s
        for s in failure_counter.samples
        if s.labels.get("name") == unique_name and s.value == 5.0
    ).value
    assert failure_value == 5.0

    # Reset circuit breaker to test success counter
    cb.reset()

    # Success counter (metric name is 'circuit_breaker_successes')
    cb.call(lambda: "ok")
    success_counter = next(
        m for m in REGISTRY.collect() if m.name == "circuit_breaker_successes"
    )
    success_value = next(
        s
        for s in success_counter.samples
        if s.labels.get("name") == unique_name and s.value == 1.0
    ).value
    assert success_value == 1.0


def test_circuit_breaker_thread_safety():
    """Test circuit breaker is thread-safe."""
    import threading

    config = CircuitBreakerConfig(failure_threshold=100, enabled=True)
    cb = CircuitBreaker("test_thread", config)

    def worker():
        for _ in range(50):
            cb.call(lambda: "ok")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = cb.get_stats()
    assert stats["total_calls"] == 500
    assert stats["successes"] == 500
    assert stats["state"] == "closed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
