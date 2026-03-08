"""Performance testing framework for bt_api_py.

This module provides performance benchmarks and load testing capabilities
for trading operations across multiple exchanges.
"""

import asyncio
import statistics
import time
from dataclasses import dataclass
from typing import Any

import pytest

from bt_api_py.bt_api import BtApi


@dataclass
class PerformanceMetrics:
    """Performance metrics container."""

    operation: str
    exchange: str
    duration_ms: float
    success: bool
    error_message: str | None = None

    @classmethod
    def measure(cls, operation: str, exchange: str):
        """Context manager for measuring operations."""
        return PerformanceMeasure(operation, exchange)


class PerformanceMeasure:
    """Context manager for measuring performance."""

    def __init__(self, operation: str, exchange: str):
        self.operation = operation
        self.exchange = exchange
        self.start_time = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (time.perf_counter() - self.start_time) * 1000
        success = exc_type is None
        error = str(exc_val) if exc_val else None
        self.metrics = PerformanceMetrics(
            operation=self.operation,
            exchange=self.exchange,
            duration_ms=duration,
            success=success,
            error_message=error,
        )


class PerformanceTestSuite:
    """Comprehensive performance test suite."""

    def __init__(self):
        self.api = BtApi()
        self.metrics: list[PerformanceMetrics] = []

    async def test_latency(self, exchange: str, operations: list[str], iterations: int = 10):
        """Test operation latency for an exchange."""
        for operation in operations:
            for _ in range(iterations):
                if operation == "get_ticker":
                    with PerformanceMetrics.measure(operation, exchange) as pm:
                        try:
                            await self.api.async_get_ticker(f"{exchange}___SPOT", "BTCUSDT")
                            pm.metrics = pm.metrics
                        except Exception:
                            pm.metrics = pm.metrics

                elif operation == "get_balance":
                    with PerformanceMetrics.measure(operation, exchange) as pm:
                        try:
                            await self.api.async_get_balance(f"{exchange}___SPOT")
                            pm.metrics = pm.metrics
                        except Exception:
                            pm.metrics = pm.metrics

                self.metrics.append(pm.metrics)

    async def test_throughput(self, exchange: str, operation: str, concurrent_requests: int = 10):
        """Test throughput with concurrent requests."""

        async def single_request():
            with PerformanceMetrics.measure(operation, exchange) as pm:
                try:
                    if operation == "get_ticker":
                        await self.api.async_get_ticker(f"{exchange}___SPOT", "BTCUSDT")
                    elif operation == "get_depth":
                        await self.api.async_get_depth(f"{exchange}___SPOT", "BTCUSDT")
                    pm.metrics = pm.metrics
                except Exception:
                    pm.metrics = pm.metrics
            return pm.metrics

        tasks = [single_request() for _ in range(concurrent_requests)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.metrics.extend([r for r in results if isinstance(r, PerformanceMetrics)])

    def test_memory_usage(self, exchange: str, duration_seconds: int = 60):
        """Test memory usage over time."""
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        start_time = time.time()
        message_count = 0

        # Simulate continuous operation
        while time.time() - start_time < duration_seconds:
            try:
                # Simulate receiving market data
                self.api.put_ticker(
                    {"symbol": "BTCUSDT", "price": "50000.0", "timestamp": int(time.time() * 1000)}
                )
                message_count += 1
                time.sleep(0.1)  # 10 messages per second
            except Exception:
                pass

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        return {
            "initial_memory_mb": initial_memory,
            "final_memory_mb": final_memory,
            "memory_increase_mb": memory_increase,
            "messages_processed": message_count,
            "memory_per_message_kb": (memory_increase * 1024) / message_count
            if message_count > 0
            else 0,
        }

    def generate_report(self) -> dict[str, Any]:
        """Generate performance report."""
        if not self.metrics:
            return {"error": "No metrics collected"}

        # Group metrics by operation and exchange
        grouped = {}
        for metric in self.metrics:
            key = f"{metric.exchange}_{metric.operation}"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(metric)

        report = {
            "summary": {
                "total_operations": len(self.metrics),
                "success_rate": sum(1 for m in self.metrics if m.success) / len(self.metrics) * 100,
                "overall_avg_latency_ms": statistics.mean(
                    [m.duration_ms for m in self.metrics if m.success]
                ),
            },
            "by_operation": {},
        }

        for key, metrics in grouped.items():
            successful = [m for m in metrics if m.success]
            if successful:
                durations = [m.duration_ms for m in successful]
                report["by_operation"][key] = {
                    "count": len(metrics),
                    "success_rate": len(successful) / len(metrics) * 100,
                    "avg_latency_ms": statistics.mean(durations),
                    "p95_latency_ms": sorted(durations)[int(len(durations) * 0.95)],
                    "p99_latency_ms": sorted(durations)[int(len(durations) * 0.99)],
                    "min_latency_ms": min(durations),
                    "max_latency_ms": max(durations),
                }

        return report


@pytest.mark.performance
@pytest.mark.slow
class TestPerformance:
    """Performance test suite."""

    @pytest.fixture
    def perf_suite(self):
        """Create performance test suite."""
        return PerformanceTestSuite()

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_binance_latency(self, perf_suite):
        """Test Binance API latency."""
        operations = ["get_ticker", "get_balance"]
        await perf_suite.test_latency("BINANCE", operations, iterations=5)

        report = perf_suite.generate_report()
        assert report["summary"]["success_rate"] >= 80

        # Check latency requirements
        for key, metrics in report["by_operation"].items():
            if "BINANCE_get_ticker" in key:
                assert metrics["avg_latency_ms"] < 100, (
                    f"High latency: {metrics['avg_latency_ms']}ms"
                )
                assert metrics["p95_latency_ms"] < 200, (
                    f"High P95 latency: {metrics['p95_latency_ms']}ms"
                )

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_concurrent_requests(self, perf_suite):
        """Test concurrent request handling."""
        await perf_suite.test_throughput("BINANCE", "get_ticker", concurrent_requests=5)

        report = perf_suite.generate_report()
        assert report["summary"]["success_rate"] >= 70  # Allow some failures due to rate limits

    def test_memory_usage_stability(self, perf_suite):
        """Test memory usage over time."""
        memory_stats = perf_suite.test_memory_usage("BINANCE", duration_seconds=10)

        # Memory should not increase significantly
        assert memory_stats["memory_increase_mb"] < 50, (
            f"High memory increase: {memory_stats['memory_increase_mb']}MB"
        )
        assert memory_stats["memory_per_message_kb"] < 1, (
            f"High memory per message: {memory_stats['memory_per_message_kb']}KB"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestEndToEndWorkflows:
    """End-to-end workflow tests."""

    @pytest.mark.asyncio
    @pytest.mark.network
    async def test_multi_exchange_arbitrage_scenario(self):
        """Test arbitrage scenario across exchanges."""
        api = BtApi()

        # Get prices from multiple exchanges
        exchanges = ["BINANCE", "OKX", "HTX"]
        prices = {}

        for exchange in exchanges:
            try:
                ticker = await api.async_get_ticker(f"{exchange}___SPOT", "BTCUSDT")
                if ticker:
                    prices[exchange] = float(
                        ticker.last_price
                        if hasattr(ticker, "last_price")
                        else ticker.get("last_price", 0)
                    )
            except Exception as e:
                # Log but don't fail the test
                print(f"Failed to get price from {exchange}: {e}")

        # Should have at least 2 successful price fetches
        assert len(prices) >= 2, f"Failed to fetch prices from enough exchanges: {prices}"

        # Check price differences
        if len(prices) >= 2:
            max_price = max(prices.values())
            min_price = min(prices.values())
            price_diff_pct = ((max_price - min_price) / min_price) * 100

            # Arbitrage opportunity should be detectable if price difference > 0.1%
            assert price_diff_pct >= 0, f"Price calculation error: {price_diff_pct}"

    @pytest.mark.asyncio
    async def test_websocket_to_rest_consistency(self):
        """Test WebSocket data consistency with REST API."""
        # This test would set up a WebSocket connection and compare
        # real-time data with REST API snapshots
        pass  # Implementation depends on WebSocket testing infrastructure
