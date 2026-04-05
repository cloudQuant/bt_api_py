"""
Core metrics collection and registry system.

Provides a lightweight Prometheus-like metrics system for bt_api_py.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol
from weakref import WeakSet

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class Metric(Protocol):
    """Protocol for metric types."""

    name: str

    def collect(self) -> dict[str, float]:
        """Collect current metric values."""
        ...


@dataclass
class MetricValue:
    """Single metric value with timestamp and labels."""

    value: float
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)


class Counter:
    """Counter metric that only increases."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0) -> None:
        """Increment counter by amount."""
        if amount < 0:
            raise ValueError("Counter can only be incremented")
        with self._lock:
            self._value += amount

    def get(self) -> float:
        """Get current value."""
        return self._value

    def collect(self) -> dict[str, float]:
        """Collect metric value."""
        return {self.name: self._value}

    def reset(self) -> None:
        """Reset counter to zero."""
        with self._lock:
            self._value = 0.0


class Gauge:
    """Gauge metric that can increase and decrease."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        """Set gauge value."""
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        """Increment gauge by amount."""
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        """Decrement gauge by amount."""
        with self._lock:
            self._value -= amount

    def get(self) -> float:
        """Get current value."""
        return self._value

    def collect(self) -> dict[str, float]:
        """Collect metric value."""
        return {self.name: self._value}


class Histogram:
    """Histogram metric with configurable buckets."""

    def __init__(
        self, name: str, description: str = "", buckets: list[float] | None = None
    ) -> None:
        self.name = name
        self.description = description
        self.buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self._observations: list[float] = []
        self._bucket_counts: dict[float, int] = dict.fromkeys(self.buckets, 0)
        self._bucket_counts[float("inf")] = 0
        self._count = 0
        self._sum = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        """Observe a value."""
        with self._lock:
            self._observations.append(value)
            self._count += 1
            self._sum += value

            # Update bucket counts
            for bucket in self.buckets + [float("inf")]:
                if value <= bucket:
                    self._bucket_counts[bucket] += 1

    def get_count(self) -> int:
        """Get total count of observations."""
        return self._count

    def get_sum(self) -> float:
        """Get sum of all observations."""
        return round(self._sum, 12)

    def get_average(self) -> float:
        """Get average of observations."""
        return self._sum / self._count if self._count > 0 else 0.0

    def collect(self) -> dict[str, float]:
        """Collect all histogram metrics."""
        result = {}

        # Bucket counts
        for bucket, count in self._bucket_counts.items():
            result[f'{self.name}_bucket{{le="{bucket}"}}'] = float(count)

        # Summary stats
        result[f"{self.name}_count"] = float(self._count)
        result[f"{self.name}_sum"] = self._sum

        return result


class MetricRegistry:
    """Registry for collecting and managing metrics."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._collectors: WeakSet[Callable] = WeakSet()
        self._lock = threading.Lock()

    def register(self, metric: Metric) -> Metric:
        """Register a metric."""
        with self._lock:
            if metric.name in self._metrics:
                raise ValueError(f"Metric {metric.name} already registered")
            self._metrics[metric.name] = metric
        return metric

    def unregister(self, name: str) -> None:
        """Unregister a metric."""
        with self._lock:
            self._metrics.pop(name, None)

    def get_metric(self, name: str) -> Metric | None:
        """Get metric by name."""
        return self._metrics.get(name)

    def collect_all(self) -> dict[str, float]:
        """Collect all registered metrics."""
        result = {}
        with self._lock:
            metrics_copy = self._metrics.copy()

        for metric in metrics_copy.values():
            try:
                result.update(metric.collect())
            except Exception as e:
                # Skip problematic metrics
                logger.debug("Metric %s collect failed: %s", metric.name, e)
                continue

        return result

    def clear(self) -> None:
        """Clear all metrics."""
        with self._lock:
            self._metrics.clear()


# Global registry instance
_default_registry = MetricRegistry()


def counter(name: str, description: str = "") -> Counter:
    """Create and register a counter."""
    metric = Counter(name, description)
    _default_registry.register(metric)
    return metric


def gauge(name: str, description: str = "") -> Gauge:
    """Create and register a gauge."""
    metric = Gauge(name, description)
    _default_registry.register(metric)
    return metric


def histogram(name: str, description: str = "", buckets: list[float] | None = None) -> Histogram:
    """Create and register a histogram."""
    metric = Histogram(name, description, buckets)
    _default_registry.register(metric)
    return metric


def get_registry() -> MetricRegistry:
    """Get the default metric registry."""
    return _default_registry


class PerformanceTimer:
    """High-precision performance timer."""

    def __init__(self, histogram: Histogram | None = None) -> None:
        self._histogram = histogram
        self._start_time: float | None = None
        self._end_time: float | None = None

    def start(self) -> None:
        """Start timing."""
        self._start_time = time.perf_counter()

    def stop(self) -> float:
        """Stop timing and return duration."""
        if self._start_time is None:
            raise RuntimeError("Timer not started")

        self._end_time = time.perf_counter()
        duration = self._end_time - self._start_time

        if self._histogram:
            self._histogram.observe(duration)

        return duration

    def __enter__(self) -> PerformanceTimer:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


@contextmanager
def timer(histogram: Histogram | None = None) -> Iterator[PerformanceTimer]:
    """Context manager for timing operations."""
    perf_timer = PerformanceTimer(histogram)
    with perf_timer:
        yield perf_timer
