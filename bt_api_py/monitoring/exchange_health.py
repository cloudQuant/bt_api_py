"""
Exchange health monitoring system.

Provides health checks and status monitoring for exchange connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from bt_api_py.monitoring.metrics import Counter, Gauge, Histogram, get_registry


class HealthStatus(Enum):
    """Health status of an exchange."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheck:
    """Single health check configuration."""

    name: str
    check_func: Callable[..., Any]
    timeout: float = 5.0
    interval: float = 30.0
    critical: bool = True
    threshold: int = 3  # Consecutive failures before marking unhealthy


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    name: str
    status: HealthStatus
    message: str
    timestamp: float = field(default_factory=time.time)
    response_time: float | None = None
    error: Exception | None = None


@dataclass
class ExchangeHealthSummary:
    """Summary of exchange health status."""

    exchange_name: str
    overall_status: HealthStatus
    check_results: list[HealthCheckResult]
    last_updated: float = field(default_factory=time.time)
    uptime_percentage: float = 0.0
    response_time_avg: float = 0.0


class ExchangeHealthMonitor:
    """Monitors health of exchange connections."""

    def __init__(self, exchange_name: str) -> None:
        self.exchange_name = exchange_name
        self._checks: list[HealthCheck] = []
        self._results: dict[str, deque] = {}
        self._failure_counts: dict[str, int] = {}
        self._running = False
        self._task: asyncio.Task | None = None

        # Metrics
        self.registry = get_registry()
        self.health_status_gauge = Gauge(
            f"exchange_{exchange_name}_health_status",
            f"Health status of {exchange_name} (0=unknown, 1=unhealthy, 2=degraded, 3=healthy)",
        )
        self.check_duration_histogram = Histogram(
            f"exchange_{exchange_name}_health_check_duration_seconds",
            f"Duration of health checks for {exchange_name}",
        )
        self.check_counter = Counter(
            f"exchange_{exchange_name}_health_checks_total",
            f"Total health checks performed for {exchange_name}",
        )
        self.failure_counter = Counter(
            f"exchange_{exchange_name}_health_check_failures_total",
            f"Total health check failures for {exchange_name}",
        )

        self.registry.register(self.health_status_gauge)
        self.registry.register(self.check_duration_histogram)
        self.registry.register(self.check_counter)
        self.registry.register(self.failure_counter)

    def add_check(self, check: HealthCheck) -> None:
        """Add a health check."""
        self._checks.append(check)
        self._results[check.name] = deque(maxlen=100)  # Keep last 100 results
        self._failure_counts[check.name] = 0

    def remove_check(self, name: str) -> None:
        """Remove a health check by name."""
        self._checks = [c for c in self._checks if c.name != name]
        self._results.pop(name, None)
        self._failure_counts.pop(name, None)

    async def run_check(self, check: HealthCheck) -> HealthCheckResult:
        """Run a single health check."""
        start_time = time.time()

        try:
            # Set timeout for the check
            result = await asyncio.wait_for(check.check_func(), timeout=check.timeout)
            response_time = time.time() - start_time

            # Check function can return bool, dict, or raise exception
            if isinstance(result, bool):
                status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
                message = "Check passed" if result else "Check failed"
            elif isinstance(result, dict):
                status = HealthStatus(result.get("status", HealthStatus.UNKNOWN.value))
                message = result.get("message", "No message")
            else:
                status = HealthStatus.HEALTHY
                message = "Check completed successfully"

            health_result = HealthCheckResult(
                name=check.name, status=status, message=message, response_time=response_time
            )

        except TimeoutError:
            response_time = time.time() - start_time
            health_result = HealthCheckResult(
                name=check.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check timed out after {check.timeout}s",
                response_time=response_time,
                error=TimeoutError(f"Timeout after {check.timeout}s"),
            )
        except Exception as e:
            response_time = time.time() - start_time
            health_result = HealthCheckResult(
                name=check.name,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
                response_time=response_time,
                error=e,
            )

        # Update metrics
        self.check_counter.inc()
        if check.name in self._results:
            self._results[check.name].append(health_result)

        if health_result.status != HealthStatus.HEALTHY:
            self.failure_counter.inc()
            self._failure_counts[check.name] += 1
        else:
            self._failure_counts[check.name] = 0

        # Record duration
        if health_result.response_time:
            self.check_duration_histogram.observe(health_result.response_time)

        return health_result

    async def run_all_checks(self) -> list[HealthCheckResult]:
        """Run all health checks concurrently."""
        if not self._checks:
            return []

        tasks = [self.run_check(check) for check in self._checks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to failed health checks
        health_results: list[HealthCheckResult] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                health_results.append(
                    HealthCheckResult(
                        name=self._checks[i].name,
                        status=HealthStatus.UNHEALTHY,
                        message=f"Health check error: {str(result)}",
                        error=result if isinstance(result, Exception) else None,
                    )
                )
            else:
                health_results.append(result)

        return health_results

    def get_overall_status(self) -> HealthStatus:
        """Calculate overall health status from all checks."""
        if not self._checks:
            return HealthStatus.UNKNOWN

        # Count statuses
        healthy_count = 0
        degraded_count = 0
        unhealthy_count = 0
        critical_unhealthy = 0

        for check in self._checks:
            failure_count = self._failure_counts.get(check.name, 0)

            if failure_count >= check.threshold:
                if check.critical:
                    critical_unhealthy += 1
                else:
                    degraded_count += 1
            elif failure_count > 0:
                degraded_count += 1
            else:
                healthy_count += 1

        # Determine overall status
        if critical_unhealthy > 0:
            return HealthStatus.UNHEALTHY
        elif unhealthy_count > 0 or degraded_count > 0:
            return HealthStatus.DEGRADED
        elif healthy_count == len(self._checks):
            return HealthStatus.HEALTHY
        else:
            return HealthStatus.UNKNOWN

    def get_health_summary(self) -> ExchangeHealthSummary:
        """Get comprehensive health summary."""
        results = [check_results[-1] for check_results in self._results.values() if check_results]

        overall_status = self.get_overall_status()

        # Calculate uptime percentage (last 100 checks per check type)
        total_checks = 0
        healthy_checks = 0
        total_response_time = 0
        response_time_count = 0

        for check_results in self._results.values():
            for result in check_results:
                total_checks += 1
                if result.status == HealthStatus.HEALTHY:
                    healthy_checks += 1
                if result.response_time:
                    total_response_time += result.response_time
                    response_time_count += 1

        uptime_percentage = (healthy_checks / total_checks * 100) if total_checks > 0 else 0
        avg_response_time = (
            (total_response_time / response_time_count) if response_time_count > 0 else 0
        )

        # Update gauge
        status_values = {
            HealthStatus.UNKNOWN: 0,
            HealthStatus.UNHEALTHY: 1,
            HealthStatus.DEGRADED: 2,
            HealthStatus.HEALTHY: 3,
        }
        self.health_status_gauge.set(status_values[overall_status])

        return ExchangeHealthSummary(
            exchange_name=self.exchange_name,
            overall_status=overall_status,
            check_results=results,
            uptime_percentage=uptime_percentage,
            response_time_avg=avg_response_time,
        )

    async def start_monitoring(self) -> None:
        """Start continuous health monitoring."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self) -> None:
        """Stop health monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        default_interval = 30.0
        while self._running:
            try:
                await self.run_all_checks()
                interval = (
                    min(c.interval for c in self._checks) if self._checks else default_interval
                )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                # Log error but continue monitoring
                await asyncio.sleep(5.0)  # Wait before retrying


class HealthCheckFactory:
    """Factory for creating common health checks."""

    @staticmethod
    def api_ping_check(api_client, endpoint: str = "/ping") -> HealthCheck:
        """Create an API ping health check."""

        async def ping_check():
            try:
                response = await api_client.get(endpoint)
                return response.status_code == 200
            except Exception:
                return False

        return HealthCheck(name="api_ping", check_func=ping_check, timeout=5.0, interval=30.0)

    @staticmethod
    def websocket_connection_check(websocket_client) -> HealthCheck:
        """Create a WebSocket connection health check."""

        async def ws_check():
            try:
                return websocket_client.is_connected()
            except Exception:
                return False

        return HealthCheck(
            name="websocket_connection", check_func=ws_check, timeout=3.0, interval=15.0
        )

    @staticmethod
    def data_freshness_check(data_source, max_age_seconds: float = 60.0) -> HealthCheck:
        """Create a data freshness health check."""

        async def freshness_check():
            try:
                last_update = await data_source.get_last_update_time()
                if last_update is None:
                    return {"status": HealthStatus.UNKNOWN.value, "message": "No data available"}

                age = time.time() - last_update
                if age <= max_age_seconds:
                    return True
                else:
                    return {
                        "status": HealthStatus.DEGRADED.value,
                        "message": f"Data is {age:.1f}s old (max {max_age_seconds}s)",
                    }
            except Exception as e:
                return {"status": HealthStatus.UNHEALTHY.value, "message": str(e)}

        return HealthCheck(
            name="data_freshness",
            check_func=freshness_check,
            timeout=5.0,
            interval=30.0,
            critical=False,
        )

    @staticmethod
    def rate_limit_check(rate_limiter, threshold: float = 0.8) -> HealthCheck:
        """Create a rate limit usage health check."""

        async def rate_limit_check_func():
            try:
                usage = await rate_limiter.get_usage_percentage()
                if usage <= threshold:
                    return True
                else:
                    return {
                        "status": HealthStatus.DEGRADED.value,
                        "message": f"Rate limit usage at {usage:.1%} (threshold {threshold:.1%})",
                    }
            except Exception as e:
                return {"status": HealthStatus.UNHEALTHY.value, "message": str(e)}

        return HealthCheck(
            name="rate_limit",
            check_func=rate_limit_check_func,
            timeout=2.0,
            interval=10.0,
            critical=False,
        )
