"""
Example usage of the advanced WebSocket system for bt_api_py.
Demonstrates high-performance WebSocket connections with monitoring and error handling.
"""

import asyncio
import logging
from typing import Dict, Any

from bt_api_py.websocket import (
    get_websocket_manager,
    get_websocket_monitor,
    subscribe_to_ticker,
    subscribe_to_depth,
    subscribe_to_trades,
    subscribe_to_klines,
    get_websocket_stats,
    get_monitoring_dashboard,
    WebSocketConfig,
    PoolConfiguration,
    ExchangeCredentials,
    AuthenticationType,
    PerformanceAlert,
    AlertSeverity,
)


# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TradingDataHandler:
    """Example handler for trading data."""

    def __init__(self):
        self.ticker_data = {}
        self.depth_data = {}
        self.trade_data = {}
        self.kline_data = {}

    async def handle_ticker_data(self, data: Dict[str, Any]) -> None:
        """Handle ticker data updates."""
        symbol = data.get("symbol")
        if symbol:
            self.ticker_data[symbol] = data

            # Extract normalized fields
            last_price = data.get("last_price", 0)
            volume = data.get("volume", 0)
            change_24h = data.get("change_24h", 0)

            logger.info(f"Ticker {symbol}: ${last_price:.2f} ({change_24h:+.2f}%) Volume: {volume}")

    async def handle_depth_data(self, data: Dict[str, Any]) -> None:
        """Handle order book depth updates."""
        symbol = data.get("symbol")
        if symbol:
            self.depth_data[symbol] = data

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if bids and asks:
                best_bid = bids[0][0] if bids else 0
                best_ask = asks[0][0] if asks else 0
                spread = best_ask - best_bid

                logger.info(
                    f"Depth {symbol}: Bid ${best_bid:.2f} Ask ${best_ask:.2f} Spread ${spread:.2f}"
                )

    async def handle_trade_data(self, data: Dict[str, Any]) -> None:
        """Handle trade data updates."""
        symbol = data.get("symbol")
        if symbol:
            trade_info = {
                "symbol": symbol,
                "price": data.get("price", 0),
                "quantity": data.get("quantity", 0),
                "timestamp": data.get("timestamp", 0),
                "side": data.get("side", "unknown"),
            }

            # Store recent trades (keep last 100)
            if symbol not in self.trade_data:
                self.trade_data[symbol] = []

            self.trade_data[symbol].append(trade_info)
            if len(self.trade_data[symbol]) > 100:
                self.trade_data[symbol].pop(0)

            logger.info(
                f"Trade {symbol}: {trade_info['side']} {trade_info['quantity']} @ ${trade_info['price']:.2f}"
            )

    async def handle_kline_data(self, data: Dict[str, Any]) -> None:
        """Handle candlestick/kline data updates."""
        symbol = data.get("symbol")
        if symbol:
            kline_info = {
                "symbol": symbol,
                "open": data.get("open", 0),
                "high": data.get("high", 0),
                "low": data.get("low", 0),
                "close": data.get("close", 0),
                "volume": data.get("volume", 0),
                "timestamp": data.get("timestamp", 0),
                "is_closed": data.get("is_closed", False),
            }

            # Store klines by symbol and interval
            if symbol not in self.kline_data:
                self.kline_data[symbol] = {}

            # Use timestamp as key for now (in real implementation, would use interval)
            interval_key = str(kline_info["timestamp"])
            self.kline_data[symbol][interval_key] = kline_info

            if kline_info["is_closed"]:
                change_pct = ((kline_info["close"] - kline_info["open"]) / kline_info["open"]) * 100
                logger.info(
                    f"Kline {symbol} closed: ${kline_info['close']:.2f} ({change_pct:+.2f}%)"
                )
            else:
                logger.info(f"Kline {symbol} update: ${kline_info['close']:.2f}")


class WebSocketPerformanceMonitor:
    """Custom performance monitoring for WebSocket connections."""

    def __init__(self):
        self.alert_count = 0
        self.performance_data = {}

    async def handle_performance_alert(self, alert: PerformanceAlert) -> None:
        """Handle performance alerts."""
        self.alert_count += 1
        logger.warning(
            f"Performance Alert #{self.alert_count}: {alert.name} ({alert.severity.value})"
        )
        logger.warning(f"Description: {alert.description}")
        logger.warning(f"Condition: {alert.condition} > {alert.threshold}")
        logger.warning(f"Triggered count: {alert.triggered_count}")

    async def log_periodic_stats(self) -> None:
        """Log periodic statistics."""
        while True:
            try:
                # Get WebSocket stats
                ws_stats = await get_websocket_stats()

                # Get monitoring dashboard
                dashboard = await get_monitoring_dashboard()

                logger.info("=== WebSocket Statistics ===")
                logger.info(f"Total connections: {ws_stats['global_metrics']['total_connections']}")
                logger.info(
                    f"Active connections: {ws_stats['global_metrics']['active_connections']}"
                )
                logger.info(
                    f"Total subscriptions: {ws_stats['global_metrics']['total_subscriptions']}"
                )
                logger.info(f"Messages sent: {ws_stats['global_metrics']['total_messages_sent']}")
                logger.info(
                    f"Messages received: {ws_stats['global_metrics']['total_messages_received']}"
                )
                logger.info(f"Active alerts: {dashboard['active_alerts']}")

                # Log per-exchange stats
                for exchange_name, exchange_stats in ws_stats["pools"].items():
                    logger.info(
                        f"  {exchange_name}: {exchange_stats['active_connections']} connections, {exchange_stats['total_subscriptions']} subscriptions"
                    )

                logger.info("========================")

                await asyncio.sleep(60)  # Log every minute

            except Exception as e:
                logger.error(f"Error in periodic stats logging: {e}")
                await asyncio.sleep(60)


async def setup_exchange_with_credentials(exchange_name: str) -> None:
    """Setup exchange with authentication credentials."""
    manager = await get_websocket_manager()
    monitor = await get_websocket_monitor()

    # Create credentials for exchanges that require authentication
    if exchange_name.upper() in ["BINANCE", "OKX"]:
        # Example credentials - in production, these would come from secure config
        credentials = ExchangeCredentials(
            exchange_name=exchange_name.upper(),
            auth_type=AuthenticationType.API_KEY_SECRET,
            api_key="your_api_key_here",
            api_secret="your_api_secret_here",
            passphrase="your_passphrase_here",  # Required for OKX
        )
    else:
        credentials = None

    # Create pool configuration for performance optimization
    pool_config = PoolConfiguration(
        min_connections=2,
        max_connections=8,
        load_balance_strategy="weighted",  # Use weighted load balancing
        health_check_interval=30.0,
        connection_max_age=1800.0,  # 30 minutes
        failover_enabled=True,
        metrics_enabled=True,
        detailed_logging=False,
    )

    # Create WebSocket configuration
    from bt_api_py.websocket.exchange_adapters import WebSocketAdapterFactory

    adapter = WebSocketAdapterFactory.create_adapter(exchange_name, credentials=credentials)
    endpoints = adapter.get_endpoints(f"wss://stream.{exchange_name.lower()}.com")

    config = WebSocketConfig(
        url=endpoints[0],
        exchange_name=exchange_name,
        endpoints=endpoints[1:],  # Failover endpoints
        max_connections=pool_config.max_connections,
        min_connections=pool_config.min_connections,
        heartbeat_interval=30.0,
        reconnect_enabled=True,
        reconnect_interval=1.0,
        max_reconnect_attempts=10,
        compression=True,
        rate_limit_enabled=True,
        subscription_limits=adapter.get_subscription_limits(),
        exchange_config={"requires_auth": credentials is not None, "credentials": credentials},
    )

    # Add exchange to manager
    await manager.add_exchange(config, pool_config)

    # Add custom alert
    if exchange_name.upper() == "BINANCE":
        custom_alert = PerformanceAlert(
            alert_id=f"{exchange_name}_high_latency",
            name=f"{exchange_name} High Latency Alert",
            severity=AlertSeverity.WARNING,
            condition="avg_latency_ms",
            threshold=200.0,  # 200ms threshold for Binance
            description=f"{exchange_name} average latency exceeds 200ms",
        )
        monitor.alert_manager.add_alert(custom_alert)

    logger.info(f"Setup complete for {exchange_name}")


async def example_basic_subscription():
    """Example: Basic WebSocket subscription with automatic setup."""
    logger.info("=== Basic Subscription Example ===")

    # Create data handler
    handler = TradingDataHandler()

    # Subscribe to BTC ticker on Binance (automatic setup)
    subscription_id = await subscribe_to_ticker(
        exchange_name="BINANCE", symbol="BTCUSDT", callback=handler.handle_ticker_data
    )

    logger.info(f"Subscribed to BTCUSDT ticker with ID: {subscription_id}")

    # Subscribe to ETH ticker
    eth_subscription_id = await subscribe_to_ticker(
        exchange_name="BINANCE", symbol="ETHUSDT", callback=handler.handle_ticker_data
    )

    logger.info(f"Subscribed to ETHUSDT ticker with ID: {eth_subscription_id}")

    # Run for 60 seconds
    await asyncio.sleep(60)

    # Unsubscribe
    from bt_api_py.websocket import unsubscribe

    await unsubscribe("BINANCE", subscription_id)
    await unsubscribe("BINANCE", eth_subscription_id)

    logger.info("Unsubscribed from tickers")


async def example_advanced_subscription():
    """Example: Advanced WebSocket subscription with custom configuration."""
    logger.info("=== Advanced Subscription Example ===")

    # Setup exchanges with custom configuration
    await setup_exchange_with_credentials("BINANCE")
    await setup_exchange_with_credentials("OKX")

    # Create data handler
    handler = TradingDataHandler()

    # Get manager and monitor for advanced operations
    manager = await get_websocket_manager()
    monitor = await get_websocket_monitor()

    # Add performance monitoring
    perf_monitor = WebSocketPerformanceMonitor()
    monitor.alert_manager.add_notification_handler(perf_monitor.handle_performance_alert)

    # Start periodic stats logging
    stats_task = asyncio.create_task(perf_monitor.log_periodic_stats())

    try:
        # Subscribe to multiple data types on Binance
        btc_ticker_id = await manager.subscribe(
            "BINANCE", "ticker", "BTCUSDT", handler.handle_ticker_data
        )

        btc_depth_id = await manager.subscribe(
            "BINANCE", "depth", "BTCUSDT", handler.handle_depth_data, {"level": "10"}
        )

        btc_trades_id = await manager.subscribe(
            "BINANCE", "trades", "BTCUSDT", handler.handle_trade_data
        )

        btc_klines_id = await manager.subscribe(
            "BINANCE", "kline", "BTCUSDT", handler.handle_kline_data, {"interval": "1m"}
        )

        # Subscribe to data on OKX
        eth_ticker_id = await manager.subscribe(
            "OKX", "ticker", "ETH-USDT", handler.handle_ticker_data
        )

        eth_depth_id = await manager.subscribe(
            "OKX", "depth", "ETH-USDT", handler.handle_depth_data
        )

        logger.info("Subscribed to multiple data streams")
        logger.info("Running for 5 minutes...")

        # Run for 5 minutes
        await asyncio.sleep(300)

        # Show final statistics
        final_stats = await get_websocket_stats()
        dashboard = await get_monitoring_dashboard()

        logger.info("=== Final Statistics ===")
        logger.info(f"Total connections: {final_stats['global_metrics']['total_connections']}")
        logger.info(
            f"Total messages received: {final_stats['global_metrics']['total_messages_received']}"
        )
        logger.info(f"Total alerts triggered: {perf_monitor.alert_count}")

        # Show subscription data collected
        logger.info(f"Ticker data collected: {len(handler.ticker_data)} symbols")
        logger.info(f"Depth data collected: {len(handler.depth_data)} symbols")
        logger.info(f"Trade data collected: {len(handler.trade_data)} symbols")
        logger.info(f"Kline data collected: {len(handler.kline_data)} symbols")

        # Clean up subscriptions
        await manager.unsubscribe("BINANCE", btc_ticker_id)
        await manager.unsubscribe("BINANCE", btc_depth_id)
        await manager.unsubscribe("BINANCE", btc_trades_id)
        await manager.unsubscribe("BINANCE", btc_klines_id)
        await manager.unsubscribe("OKX", eth_ticker_id)
        await manager.unsubscribe("OKX", eth_depth_id)

    finally:
        # Stop stats logging
        stats_task.cancel()
        try:
            await stats_task
        except asyncio.CancelledError:
            pass


async def example_performance_benchmark():
    """Example: Performance benchmarking."""
    logger.info("=== Performance Benchmark Example ===")

    monitor = await get_websocket_monitor()

    # Run latency benchmark
    logger.info("Running latency benchmark...")
    latency_result = await monitor.benchmark.run_latency_benchmark(
        await get_websocket_manager(), duration=30.0
    )

    if latency_result.success:
        logger.info(f"Latency benchmark results:")
        for metric, value in latency_result.metrics.items():
            logger.info(f"  {metric}: {value}")
    else:
        logger.error(f"Latency benchmark failed: {latency_result.error_message}")

    # Run throughput benchmark
    logger.info("Running throughput benchmark...")
    throughput_result = await monitor.benchmark.run_throughput_benchmark(
        await get_websocket_manager(), duration=30.0
    )

    if throughput_result.success:
        logger.info(f"Throughput benchmark results:")
        for metric, value in throughput_result.metrics.items():
            logger.info(f"  {metric}: {value}")
    else:
        logger.error(f"Throughput benchmark failed: {throughput_result.error_message}")

    # Run memory benchmark
    logger.info("Running memory benchmark...")
    memory_result = await monitor.benchmark.run_memory_benchmark(
        await get_websocket_manager(), duration=30.0
    )

    if memory_result.success:
        logger.info(f"Memory benchmark results:")
        for metric, value in memory_result.metrics.items():
            logger.info(f"  {metric}: {value}")
    else:
        logger.error(f"Memory benchmark failed: {memory_result.error_message}")


async def example_error_handling():
    """Example: Error handling and recovery."""
    logger.info("=== Error Handling Example ===")

    manager = await get_websocket_manager()

    # Create a configuration with invalid endpoint to test error handling
    config = WebSocketConfig(
        url="wss://invalid-endpoint.com",  # Invalid endpoint
        exchange_name="TEST",
        max_reconnect_attempts=3,
        reconnect_interval=1.0,
    )

    pool_config = PoolConfiguration(min_connections=1, max_connections=2, failover_enabled=True)

    try:
        # Add exchange with invalid config
        await manager.add_exchange(config, pool_config)

        # Try to subscribe - should fail and demonstrate error handling
        handler = TradingDataHandler()

        subscription_id = await manager.subscribe(
            "TEST", "ticker", "INVALID", handler.handle_ticker_data
        )

        logger.info("This should not be reached due to connection failure")

    except Exception as e:
        logger.error(f"Expected error occurred: {e}")
        logger.info("Error handling and circuit breaker worked as expected")

    # Demonstrate graceful degradation
    logger.info("Demonstrating graceful degradation...")

    # Setup a valid exchange
    await setup_exchange_with_credentials("BINANCE")

    # Subscribe to valid data
    handler = TradingDataHandler()
    subscription_id = await manager.subscribe(
        "BINANCE", "ticker", "BTCUSDT", handler.handle_ticker_data
    )

    logger.info("Successfully subscribed with valid configuration")

    # Run for a short time
    await asyncio.sleep(30)

    # Clean up
    await manager.unsubscribe("BINANCE", subscription_id)


async def main():
    """Main example runner."""
    logger.info("Starting Advanced WebSocket Examples")

    try:
        # Run examples
        await example_basic_subscription()
        await asyncio.sleep(2)

        await example_advanced_subscription()
        await asyncio.sleep(2)

        await example_performance_benchmark()
        await asyncio.sleep(2)

        await example_error_handling()

    except KeyboardInterrupt:
        logger.info("Examples interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error in examples: {e}")
    finally:
        # Cleanup
        from bt_api_py.websocket import _websocket_system

        await _websocket_system.shutdown()

        logger.info("WebSocket system shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
