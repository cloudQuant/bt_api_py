"""
Usage examples for bt_api_py monitoring system.

Demonstrates how to use monitoring, logging, and observability features.
"""

import asyncio
import time

from bt_api_py.logging_system import get_logging_manager
from bt_api_py.monitoring import (
    ExchangeHealthMonitor,
    HealthCheckFactory,
    counter,
    gauge,
    get_business_collector,
    get_logger,
    histogram,
    monitor_async_performance,
    monitor_calls,
    monitor_execution_time,
    monitor_performance,
    setup_logging_for_production,
    timer,
)


# Example 1: Basic function monitoring
@monitor_performance("example.calculate_price")
def calculate_price(symbol: str, quantity: float) -> float:
    """Example function with performance monitoring."""
    # Simulate some work
    time.sleep(0.01)
    return quantity * 100.0  # Mock price calculation


# Example 2: Lightweight execution time monitoring
@monitor_execution_time("example.simple_calculation")
def simple_calculation(x: int, y: int) -> int:
    """Example with just execution time monitoring."""
    return x * y + 10


# Example 3: Call counting with success tracking
@monitor_calls("example.api_call", track_success=True)
async def api_call(endpoint: str) -> dict:
    """Example API call with monitoring."""
    # Simulate API call
    await asyncio.sleep(0.05)

    # Simulate occasional failure
    import random

    if random.random() < 0.1:  # 10% failure rate
        raise Exception("API call failed")

    return {"status": "success", "data": {"value": 42}}


# Example 4: Async function monitoring
@monitor_async_performance("example.async_operation")
async def async_operation(delay: float) -> str:
    """Example async function with monitoring."""
    await asyncio.sleep(delay)
    return f"Completed after {delay}s"


# Example 5: Custom metrics usage
def demonstrate_custom_metrics():
    """Demonstrate custom metric usage."""
    # Create custom metrics
    request_counter = counter("example_requests_total", "Total requests")
    active_users_gauge = gauge("example_active_users", "Current active users")
    response_histogram = histogram(
        "example_response_time_seconds",
        "Response time distribution",
        buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    )

    # Use metrics
    request_counter.inc()
    active_users_gauge.set(150)
    response_histogram.observe(0.15)

    # Use timer context manager
    with timer(response_histogram):
        # Simulate operation
        time.sleep(0.12)


# Example 6: Exchange health monitoring
async def setup_exchange_health_monitoring():
    """Setup exchange health monitoring."""
    # Create health monitor for Binance
    binance_monitor = ExchangeHealthMonitor("BINANCE")

    # Add API ping check
    api_check = HealthCheckFactory.api_ping_check(None, "/api/v3/ping")
    binance_monitor.add_check(api_check)

    # Add data freshness check
    class MockDataSource:
        async def get_last_update_time(self):
            return time.time() - 30  # 30 seconds ago

    data_source = MockDataSource()
    freshness_check = HealthCheckFactory.data_freshness_check(data_source, max_age_seconds=60)
    binance_monitor.add_check(freshness_check)

    # Start monitoring
    await binance_monitor.start_monitoring()

    # Get health status
    summary = binance_monitor.get_health_summary()
    print(f"Binance health status: {summary.overall_status}")

    return binance_monitor


# Example 7: Structured logging
def demonstrate_structured_logging():
    """Demonstrate structured logging."""
    logger = get_logger("example")

    # Generate correlation ID for request
    logging_manager = get_logging_manager()
    correlation_id = logging_manager.generate_correlation_id()

    with logging_manager.with_correlation_id(correlation_id):
        logger.info("Starting processing", component="processor", step="init")

        try:
            # Log API request
            logger.api_request(
                method="GET",
                endpoint="/api/v1/ticker/BTCUSDT",
                exchange_name="BINANCE",
                status_code=200,
                duration_ms=45.5,
            )

            # Log order event
            logger.order_event(
                event_type="placed",
                exchange_name="BINANCE",
                symbol="BTCUSDT",
                side="BUY",
                quantity=0.1,
                order_id="12345",
            )

            # Log connection event
            logger.connection_event(
                event_type="connected", exchange_name="BINANCE", connection_type="websocket"
            )

        except Exception:
            logger.exception("Processing failed", component="processor", step="execute")


# Example 8: Business metrics integration
def demonstrate_business_metrics():
    """Demonstrate business metrics usage."""
    business_metrics = get_business_collector()

    # Record order placement
    business_metrics.record_order_placed(success=True, latency=0.025)

    # Record API request
    business_metrics.record_api_request(success=True, latency=0.045)

    # Record market data update
    business_metrics.record_market_data_update()

    # Set active connections
    business_metrics.set_active_connections(5)


# Example 9: Complete trading scenario
class TradingBotExample:
    """Example trading bot with integrated monitoring."""

    def __init__(self):
        self.logger = get_logger("trading_bot")
        self.business_metrics = get_business_collector()
        self.health_monitor: ExchangeHealthMonitor = None

    async def initialize(self):
        """Initialize the bot with monitoring."""
        self.logger.info("Initializing trading bot")

        # Setup health monitoring
        self.health_monitor = await setup_exchange_health_monitoring()

        # Set initial connection count
        self.business_metrics.set_active_connections(1)

    @monitor_performance("trading_bot.place_order")
    async def place_order(self, symbol: str, side: str, quantity: float):
        """Place an order with monitoring."""
        start_time = time.time()

        try:
            # Log order placement attempt
            self.logger.order_event(
                event_type="placing",
                exchange_name="BINANCE",
                symbol=symbol,
                side=side,
                quantity=quantity,
            )

            # Simulate order placement
            await asyncio.sleep(0.02)

            # Log success
            latency = (time.time() - start_time) * 1000  # Convert to ms
            self.logger.order_event(
                event_type="placed",
                exchange_name="BINANCE",
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_id="mock_order_123",
            )

            # Record metrics
            self.business_metrics.record_order_placed(success=True, latency=latency / 1000)

            return {"order_id": "mock_order_123", "status": "filled"}

        except Exception as e:
            latency = (time.time() - start_time) * 1000
            self.logger.order_event(
                event_type="failed",
                exchange_name="BINANCE",
                symbol=symbol,
                side=side,
                quantity=quantity,
                error=str(e),
            )

            self.business_metrics.record_order_placed(success=False, latency=latency / 1000)
            raise

    @monitor_execution_time("trading_bot.process_market_data")
    def process_market_data(self, data: dict):
        """Process market data with timing."""
        self.logger.market_data_event(
            event_type="received",
            exchange_name="BINANCE",
            symbol=data.get("symbol"),
            data_type="ticker",
        )

        # Update business metrics
        self.business_metrics.record_market_data_update()

        # Process the data
        # ... processing logic here ...

    async def run(self):
        """Run the trading bot."""
        await self.initialize()

        # Run trading loop
        for i in range(10):
            try:
                # Place some orders
                await self.place_order("BTCUSDT", "BUY", 0.01)
                await self.place_order("ETHUSDT", "SELL", 0.1)

                # Process market data
                self.process_market_data({"symbol": "BTCUSDT", "price": 50000})
                self.process_market_data({"symbol": "ETHUSDT", "price": 3000})

                await asyncio.sleep(1)

            except Exception:
                self.logger.exception("Error in trading loop")

        # Cleanup
        if self.health_monitor:
            await self.health_monitor.stop_monitoring()


# Example 10: Running everything together
async def main():
    """Main example runner."""
    # Setup logging
    setup_logging_for_production(log_level="INFO")
    logger = get_logger(__name__)

    logger.info("Starting monitoring examples")

    try:
        # Run basic examples
        print("=== Basic Function Monitoring ===")
        result = calculate_price("BTCUSDT", 0.1)
        print(f"Price calculation result: {result}")

        result = simple_calculation(5, 3)
        print(f"Simple calculation result: {result}")

        # Run async examples
        print("\n=== Async Function Monitoring ===")
        result = await async_operation(0.1)
        print(f"Async operation result: {result}")

        result = await api_call("/test")
        print(f"API call result: {result}")

        # Demonstrate custom metrics
        print("\n=== Custom Metrics ===")
        demonstrate_custom_metrics()

        # Demonstrate structured logging
        print("\n=== Structured Logging ===")
        demonstrate_structured_logging()

        # Demonstrate business metrics
        print("\n=== Business Metrics ===")
        demonstrate_business_metrics()

        # Run trading bot example
        print("\n=== Trading Bot Example ===")
        bot = TradingBotExample()
        await bot.run()

        logger.info("All examples completed successfully")

    except Exception:
        logger.exception("Error running examples")
        raise


if __name__ == "__main__":
    # Run the examples
    asyncio.run(main())
