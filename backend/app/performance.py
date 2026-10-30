from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import timedelta
from threading import Lock
from time import perf_counter


@dataclass(slots=True)
class RequestMetrics:
    durations_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add_duration(self, phase: str, milliseconds: float) -> None:
        with self._lock:
            self.durations_ms[phase] = self.durations_ms.get(phase, 0.0) + milliseconds

    def increment(self, counter: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[counter] = self.counters.get(counter, 0) + amount

    def snapshot(self) -> dict[str, float | int]:
        with self._lock:
            return {
                **{f"{name}_ms": round(value, 1) for name, value in self.durations_ms.items()},
                **self.counters,
            }

    def server_timing_value(self) -> str | None:
        allowed = ("app", "auth", "db", "cache", "google")
        with self._lock:
            parts = [
                f"{name};dur={self.durations_ms[name]:.1f}"
                for name in allowed
                if self.durations_ms.get(name, 0) > 0
            ]
        return ", ".join(parts) or None


_metrics_var: ContextVar[RequestMetrics | None] = ContextVar("request_metrics", default=None)
_provider_retry_deadline_var: ContextVar[float | None] = ContextVar(
    "provider_retry_deadline", default=None
)


@dataclass(frozen=True, slots=True)
class RequestPerformanceTokens:
    metrics: Token[RequestMetrics | None]
    provider_deadline: Token[float | None]


def begin_request_metrics() -> RequestPerformanceTokens:
    return RequestPerformanceTokens(
        metrics=_metrics_var.set(RequestMetrics()),
        provider_deadline=_provider_retry_deadline_var.set(perf_counter() + 20.0),
    )


def reset_request_metrics(tokens: RequestPerformanceTokens) -> None:
    _provider_retry_deadline_var.reset(tokens.provider_deadline)
    _metrics_var.reset(tokens.metrics)


def current_request_metrics() -> RequestMetrics | None:
    return _metrics_var.get()


def add_duration(phase: str, milliseconds: float) -> None:
    metrics = current_request_metrics()
    if metrics is not None:
        metrics.add_duration(phase, milliseconds)


def increment(counter: str, amount: int = 1) -> None:
    metrics = current_request_metrics()
    if metrics is not None:
        metrics.increment(counter, amount)


@contextmanager
def timed(phase: str) -> Generator[None]:
    started_at = perf_counter()
    try:
        yield
    finally:
        add_duration(phase, (perf_counter() - started_at) * 1000)


def snapshot() -> dict[str, float | int]:
    metrics = current_request_metrics()
    if metrics is None:
        return {}
    return metrics.snapshot()


def server_timing_value() -> str | None:
    metrics = current_request_metrics()
    if metrics is None:
        return None
    return metrics.server_timing_value()


def provider_retry_remaining_seconds() -> float | None:
    deadline = _provider_retry_deadline_var.get()
    return None if deadline is None else max(deadline - perf_counter(), 0.0)


@contextmanager
def provider_retry_budget(duration: timedelta) -> Generator[None]:
    seconds = duration.total_seconds()
    if seconds < 1:
        msg = "Provider retry budget must allow at least one request attempt"
        raise ValueError(msg)
    token = _provider_retry_deadline_var.set(perf_counter() + seconds)
    try:
        yield
    finally:
        _provider_retry_deadline_var.reset(token)
