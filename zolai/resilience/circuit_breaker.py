"""Circuit breaker implementation for external API calls.

Provides CLOSED/OPEN/HALF_OPEN states with configurable thresholds,
decorator and context manager interfaces, and Prometheus metrics.

States:
- CLOSED: Normal operation, failures counted
- OPEN: Failing fast, rejecting calls immediately
- HALF_OPEN: Testing recovery with limited success threshold

Configuration via environment:
- ZOLAI_CB_FAILURE_THRESHOLD=5 (failures before OPEN)
- ZOLAI_CB_TIMEOUT=30 (seconds before HALF_OPEN)
- ZOLAI_CB_SUCCESS_THRESHOLD=2 (successes in HALF_OPEN before CLOSED)
- ZOLAI_CB_ENABLED=true (feature flag to disable entirely)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker instance."""

    failure_threshold: int = 5
    timeout: float = 30.0
    success_threshold: int = 2
    enabled: bool = True

    @classmethod
    def from_env(cls, prefix: str = "ZOLAI_CB") -> "CircuitBreakerConfig":
        """Create config from environment variables."""
        return cls(
            failure_threshold=int(os.environ.get(f"{prefix}_FAILURE_THRESHOLD", "5")),
            timeout=float(os.environ.get(f"{prefix}_TIMEOUT", "30")),
            success_threshold=int(os.environ.get(f"{prefix}_SUCCESS_THRESHOLD", "2")),
            enabled=os.environ.get(f"{prefix}_ENABLED", "true").lower() != "false",
        )


@dataclass
class CircuitBreakerStats:
    """Runtime statistics for a circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    successes: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.monotonic)
    total_calls: int = 0
    rejected_calls: int = 0
    _failure_recorded: bool = False  # Tracks if a real failure has been recorded


# Prometheus metrics
_CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Current state of circuit breaker (0=closed, 1=open, 2=half_open)",
    ["name"],
)

_CIRCUIT_BREAKER_FAILURES_TOTAL = Counter(
    "circuit_breaker_failures_total",
    "Total number of failures recorded by circuit breaker",
    ["name"],
)

_CIRCUIT_BREAKER_SUCCESSES_TOTAL = Counter(
    "circuit_breaker_successes_total",
    "Total number of successes recorded by circuit breaker",
    ["name"],
)


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is OPEN and rejects a call."""

    def __init__(self, name: str, message: str = "") -> None:
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN: {message}")


class CircuitBreaker:
    """Thread-safe circuit breaker with async compatibility.

    Usage as decorator:
        @circuit_breaker("external_api")
        def call_api(): ...

    Usage as context manager:
        with circuit_breaker("external_api"):
            call_api()

    Direct usage:
        cb = CircuitBreaker("external_api")
        cb.call(func, *args, **kwargs)
    """

    _instances: dict[str, "CircuitBreaker"] = {}
    _instances_lock = threading.Lock()

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig.from_env()
        self._stats = CircuitBreakerStats()
        self._lock = threading.RLock()

        # Initialize metrics
        _CIRCUIT_BREAKER_STATE.labels(name=self.name).set(
            self._state_to_gauge(self._stats.state)
        )

    @classmethod
    def get_or_create(
        cls,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> "CircuitBreaker":
        """Get or create a circuit breaker instance (singleton per name)."""
        with cls._instances_lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name, config)
            return cls._instances[name]

    def _state_to_gauge(self, state: CircuitState) -> float:
        """Convert state to gauge value."""
        return {"closed": 0.0, "open": 1.0, "half_open": 2.0}[state.value]

    def _update_metrics(self) -> None:
        """Update Prometheus metrics from current stats."""
        _CIRCUIT_BREAKER_STATE.labels(name=self.name).set(
            self._state_to_gauge(self._stats.state)
        )

    def _should_attempt_reset(self) -> bool:
        """Check if timeout has elapsed to transition to HALF_OPEN."""
        return (
            self._stats.state == CircuitState.OPEN
            and self._stats._failure_recorded
            and time.monotonic() - self._stats.last_failure_time >= self.config.timeout
        )

    def _record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if not self.config.enabled:
                return
            self._stats.successes += 1
            self._stats.total_calls += 1
            _CIRCUIT_BREAKER_SUCCESSES_TOTAL.labels(name=self.name).inc()

            if self._stats.state == CircuitState.HALF_OPEN:
                if self._stats.successes >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)

    def _record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            if not self.config.enabled:
                return
            self._stats.failures += 1
            self._stats.total_calls += 1
            self._stats.last_failure_time = time.monotonic()
            self._stats._failure_recorded = True
            _CIRCUIT_BREAKER_FAILURES_TOTAL.labels(name=self.name).inc()

            if self._stats.state == CircuitState.CLOSED:
                if self._stats.failures >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
            elif self._stats.state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN goes back to OPEN
                self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._stats.state
        self._stats.state = new_state
        self._stats.last_state_change = time.monotonic()

        if new_state == CircuitState.CLOSED:
            self._stats.failures = 0
            self._stats.successes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._stats.successes = 0
        # OPEN state keeps failure count

        self._update_metrics()
        logger.info(
            "Circuit breaker '%s' transitioned: %s -> %s",
            self.name,
            old_state.value,
            new_state.value,
        )

    def _check_state(self) -> None:
        """Check and update state before a call."""
        with self._lock:
            if not self.config.enabled:
                return

            if self._should_attempt_reset():
                self._transition_to(CircuitState.HALF_OPEN)

            if self._stats.state == CircuitState.OPEN:
                self._stats.rejected_calls += 1
                raise CircuitBreakerError(
                    self.name,
                    f"Circuit is OPEN (failures: {self._stats.failures}, "
                    f"timeout: {self.config.timeout}s)",
                )

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute a function with circuit breaker protection."""
        self._check_state()

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    async def acall(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute an async function with circuit breaker protection."""
        self._check_state()

        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    @contextmanager
    def protect(self):
        """Context manager for circuit breaker protection.

        Usage:
            with cb.protect():
                risky_operation()
        """
        self._check_state()
        try:
            yield
            self._record_success()
        except Exception:
            self._record_failure()
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get current statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._stats.state.value,
                "failures": self._stats.failures,
                "successes": self._stats.successes,
                "total_calls": self._stats.total_calls,
                "rejected_calls": self._stats.rejected_calls,
                "last_failure_time": self._stats.last_failure_time,
                "last_state_change": self._stats.last_state_change,
                "config": {
                    "failure_threshold": self.config.failure_threshold,
                    "timeout": self.config.timeout,
                    "success_threshold": self.config.success_threshold,
                    "enabled": self.config.enabled,
                },
            }

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._stats = CircuitBreakerStats()
            self._update_metrics()
            logger.info("Circuit breaker '%s' manually reset to CLOSED", self.name)

    def force_open(self) -> None:
        """Manually force the circuit breaker to OPEN state."""
        with self._lock:
            self._transition_to(CircuitState.OPEN)


def circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to add circuit breaker protection to a function.

    Usage:
        @circuit_breaker("my_service")
        def call_service():
            ...

        # With custom config:
        @circuit_breaker("my_service", CircuitBreakerConfig(failure_threshold=3))
        def call_service():
            ...
    """
    cb = CircuitBreaker.get_or_create(name, config)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return cb.call(func, *args, **kwargs)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await cb.acall(func, *args, **kwargs)

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# Convenience function for getting a circuit breaker instance
def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """Get or create a circuit breaker instance."""
    return CircuitBreaker.get_or_create(name, config)


# Convenience context manager
@contextmanager
def circuit_breaker_context(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
):
    """Context manager for circuit breaker protection.

    Usage:
        with circuit_breaker_context("my_service"):
            risky_operation()
    """
    cb = CircuitBreaker.get_or_create(name, config)
    with cb.protect():
        yield
