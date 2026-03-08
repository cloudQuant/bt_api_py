#!/usr/bin/env python3
"""
Production-ready monitoring system demo for bt_api_py.

This script demonstrates a complete monitoring setup for a trading system.
"""

import asyncio
import time
import random
from pathlib import Path

# Setup logging for production
from bt_api_py.logging_system import setup_logging_for_production, get_logger

setup_logging_for_production()
logger = get_logger(__name__)

# Setup monitoring
from bt_api_py.monitoring import (
    monitor_performance,
    monitor_calls,
    counter,
    gauge,
    histogram,
    get_business_collector,
    start_prometheus_exporter,
    stop_prometheus_exporter,
)

# Initialize business metrics collector
business_metrics = get_business_collector()

# Create custom metrics
order_latency = histogram(
    "order_placement_duration_seconds",
    "Time to place orders",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

api_latency = histogram(
    "api_request_duration_seconds",
    "API request latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

error_counter = counter("error_total", "Total errors")
active_orders_gauge = gauge("active_orders", "Number of active orders")


@monitor_performance("trading.place_order")
@monitor_calls("place_order_calls", track_success=True)
async def place_order(symbol: str, side: str, quantity: float) -> dict:
    """Simulate placing an order with monitoring."""
    # Simulate order placement latency
    await asyncio.sleep(random.uniform(0.01, 0.05))

    # Simulate occasional failures
    if random.random() < 0.05:  # 5% failure rate
        raise Exception("Order placement failed: Insufficient balance")

    # Update metrics
    active_orders_gauge.inc()
    order_latency.observe(0.02)  # Simulated latency

    logger.order_event(
        event_type="placed",
        exchange_name="BINANCE",
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_id=f"order_{int(time.time() * 1000)}",
    )

    return {
        "order_id": f"order_{int(time.time() * 1000)}",
        "status": "filled",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": 50000.0 if symbol == "BTCUSDT" else 3000.0,
    }


@monitor_performance("market.get_ticker")
async def get_ticker(symbol: str) -> dict:
    """Simulate getting market ticker with monitoring."""
    await asyncio.sleep(random.uniform(0.005, 0.02))

    api_latency.observe(0.01)  # Simulated latency

    logger.market_data_event(
        event_type="received", exchange_name="BINANCE", symbol=symbol, data_type="ticker"
    )

    return {
        "symbol": symbol,
        "price": 50000.0 if symbol == "BTCUSDT" else 3000.0,
        "volume": random.uniform(100, 1000),
        "timestamp": time.time(),
    }


async def trading_bot():
    """Simulate a trading bot with comprehensive monitoring."""
    logger.info("Starting trading bot simulation")

    # Update business metrics
    business_metrics.set_active_connections(1)

    symbols = ["BTCUSDT", "ETHUSDT"]
    sides = ["BUY", "SELL"]

    try:
        for i in range(20):  # Run 20 iterations
            try:
                # Get market data
                ticker = await get_ticker(random.choice(symbols))
                logger.info(f"Received ticker: {ticker['symbol']} @ {ticker['price']}")

                # Place orders occasionally
                if i % 3 == 0:
                    order = await place_order(
                        random.choice(symbols), random.choice(sides), random.uniform(0.01, 0.1)
                    )
                    logger.info(f"Order placed: {order['order_id']}")

                # Simulate order completion
                if i % 5 == 0 and active_orders_gauge.get() > 0:
                    active_orders_gauge.dec()
                    logger.order_event(
                        event_type="filled",
                        exchange_name="BINANCE",
                        symbol=random.choice(symbols),
                        order_id=f"order_{int(time.time() * 1000)}",
                    )

                await asyncio.sleep(1)

            except Exception as e:
                error_counter.inc()
                logger.exception(f"Error in trading iteration {i}")

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        # Clean up metrics
        active_orders_gauge.set(0)
        business_metrics.set_active_connections(0)
        logger.info("Trading bot simulation complete")


async def monitor_system_health():
    """Monitor system health and report status."""
    from bt_api_py.monitoring import get_global_collector

    collector = get_global_collector()

    for i in range(10):  # Run for 10 iterations
        try:
            # Get current metrics
            metrics = await collector.collect_comprehensive_metrics()

            # Log system status
            logger.info(
                f"System health - CPU: {metrics.cpu_percent:.1f}%, "
                f"Memory: {metrics.memory_percent:.1f}%, "
                f"Threads: {metrics.thread_count}"
            )

            # Check for warning conditions
            if metrics.cpu_percent > 80:
                logger.warning(f"High CPU usage: {metrics.cpu_percent:.1f}%")

            if metrics.memory_percent > 85:
                logger.warning(f"High memory usage: {metrics.memory_percent:.1f}%")

            await asyncio.sleep(2)

        except Exception as e:
            error_counter.inc()
            logger.exception(f"Error in health monitoring iteration {i}")


async def main():
    """Main demonstration function."""
    logger.info("Starting BT API Py monitoring system demonstration")

    # Start Prometheus exporter
    try:
        exporter = start_prometheus_exporter(port=8080)
        logger.info(f"Prometheus metrics available at: {exporter.get_url()}")
        logger.info("Visit http://localhost:8080/metrics to see metrics")
        logger.info("Visit http://localhost:8080/health to see health status")
    except Exception as e:
        logger.error(f"Failed to start Prometheus exporter: {e}")

    # Run trading bot and health monitoring concurrently
    try:
        await asyncio.gather(trading_bot(), monitor_system_health(), return_exceptions=True)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        # Cleanup
        try:
            stop_prometheus_exporter()
            logger.info("Prometheus exporter stopped")
        except Exception:
            pass

    logger.info("Monitoring system demonstration complete")


if __name__ == "__main__":
    # Create logs directory
    Path("logs").mkdir(exist_ok=True)

    # Run the demonstration
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDemonstration stopped by user")
    except Exception as e:
        print(f"Error running demonstration: {e}")
