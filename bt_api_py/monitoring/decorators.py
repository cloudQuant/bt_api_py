"""
Performance decorators for automatic monitoring.

Provides decorators to automatically monitor function execution time,
success rates, and error rates.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

from bt_api_py.monitoring.metrics import (
    Counter,
    Histogram,
    PerformanceTimer,
    get_registry,
)

F = TypeVar("F", bound=Callable[..., Any])


def monitor_performance(
    name: str | None = None,
    registry: Any | None = None,
    include_args: bool = False,
) -> Callable[[F], F]:
    """Decorator to monitor function performance.

    Args:
        name: Custom name for the metrics. If None, uses function name.
        registry: Custom metrics registry. If None, uses default.
        include_args: Whether to include function arguments in metric names.

    Returns:
        Decorated function with monitoring.
    """

    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        metric_registry = registry or get_registry()

        # Create metrics
        duration_histogram = Histogram(
            f"{func_name}_duration_seconds",
            f"Execution time of {func_name}",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )
        calls_counter = Counter(f"{func_name}_calls_total", f"Total calls to {func_name}")
        errors_counter = Counter(f"{func_name}_errors_total", f"Total errors in {func_name}")

        # Register metrics
        metric_registry.register(duration_histogram)
        metric_registry.register(calls_counter)
        metric_registry.register(errors_counter)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            calls_counter.inc()

            try:
                with PerformanceTimer(duration_histogram):
                    result = func(*args, **kwargs)
                return result
            except Exception:
                errors_counter.inc()
                # Re-raise the exception
                raise

        # Attach metrics to function for access
        wrapper._monitoring_metrics = {  # type: ignore[attr-defined]
            "duration": duration_histogram,
            "calls": calls_counter,
            "errors": errors_counter,
        }

        return cast("F", wrapper)

    return decorator


def monitor_execution_time(
    name: str | None = None,
    registry: Any | None = None,
    buckets: list[float] | None = None,
) -> Callable[[F], F]:
    """Decorator to monitor only execution time (lighter version).

    Args:
        name: Custom name for the metric. If None, uses function name.
        registry: Custom metrics registry. If None, uses default.
        buckets: Custom buckets for the histogram.

    Returns:
        Decorated function with execution time monitoring.
    """

    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        metric_registry = registry or get_registry()

        # Create histogram metric
        duration_histogram = Histogram(
            f"{func_name}_duration_seconds",
            f"Execution time of {func_name}",
            buckets=buckets or [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

        # Register metric
        metric_registry.register(duration_histogram)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.perf_counter() - start_time
                duration_histogram.observe(duration)

        wrapper._monitoring_histogram = duration_histogram  # type: ignore[attr-defined]
        return cast("F", wrapper)

    return decorator


def monitor_calls(
    name: str | None = None,
    registry: Any | None = None,
    track_success: bool = True,
) -> Callable[[F], F]:
    """Decorator to monitor function calls and success rates.

    Args:
        name: Custom name for the metrics. If None, uses function name.
        registry: Custom metrics registry. If None, uses default.
        track_success: Whether to track successful calls separately.

    Returns:
        Decorated function with call monitoring.
    """

    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        metric_registry = registry or get_registry()

        # Create counters
        total_counter = Counter(f"{func_name}_calls_total", f"Total calls to {func_name}")

        if track_success:
            success_counter = Counter(
                f"{func_name}_success_total", f"Successful calls to {func_name}"
            )
            error_counter = Counter(f"{func_name}_error_total", f"Failed calls to {func_name}")
        else:
            success_counter = None
            error_counter = None

        # Register metrics
        metric_registry.register(total_counter)
        if success_counter:
            metric_registry.register(success_counter)
        if error_counter:
            metric_registry.register(error_counter)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            total_counter.inc()

            try:
                result = func(*args, **kwargs)
                if success_counter:
                    success_counter.inc()
                return result
            except Exception:
                if error_counter:
                    error_counter.inc()
                raise

        wrapper._monitoring_counters = {  # type: ignore[attr-defined]
            "total": total_counter,
            "success": success_counter,
            "error": error_counter,
        }

        return cast("F", wrapper)

    return decorator


def monitor_async_performance(
    name: str | None = None,
    registry: Any | None = None,
) -> Callable[[F], F]:
    """Decorator to monitor async function performance.

    Args:
        name: Custom name for the metrics. If None, uses function name.
        registry: Custom metrics registry. If None, uses default.

    Returns:
        Decorated async function with monitoring.
    """

    def decorator(func: F) -> F:
        func_name = name or f"{func.__module__}.{func.__qualname__}"
        metric_registry = registry or get_registry()

        # Create metrics
        duration_histogram = Histogram(
            f"{func_name}_duration_seconds",
            f"Execution time of async {func_name}",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        )
        calls_counter = Counter(f"{func_name}_calls_total", f"Total calls to async {func_name}")
        errors_counter = Counter(f"{func_name}_errors_total", f"Total errors in async {func_name}")

        # Register metrics
        metric_registry.register(duration_histogram)
        metric_registry.register(calls_counter)
        metric_registry.register(errors_counter)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            calls_counter.inc()

            try:
                start_time = time.perf_counter()
                result = await func(*args, **kwargs)
                duration = time.perf_counter() - start_time
                duration_histogram.observe(duration)
                return result
            except Exception:
                errors_counter.inc()
                raise

        wrapper._monitoring_metrics = {  # type: ignore[attr-defined]
            "duration": duration_histogram,
            "calls": calls_counter,
            "errors": errors_counter,
        }

        return cast("F", wrapper)

    return decorator


def get_function_metrics(func: Callable) -> dict[str, Any] | None:
    """Get monitoring metrics attached to a function.

    Args:
        func: Function to get metrics from.

    Returns:
        Dictionary of metrics if available, None otherwise.
    """
    if hasattr(func, "_monitoring_metrics"):
        return cast("dict[str, Any]", func._monitoring_metrics)
    elif hasattr(func, "_monitoring_histogram"):
        return {"histogram": func._monitoring_histogram}
    elif hasattr(func, "_monitoring_counters"):
        return cast("dict[str, Any]", func._monitoring_counters)
    return None


def reset_function_metrics(func: Callable) -> None:
    """Reset metrics for a monitored function.

    Args:
        func: Function to reset metrics for.
    """
    metrics = get_function_metrics(func)
    if not metrics:
        return

    for metric in metrics.values():
        if hasattr(metric, "reset"):
            metric.reset()
