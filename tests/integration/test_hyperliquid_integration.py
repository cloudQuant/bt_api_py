"""
# ruff: noqa  # Import verification tests - F401 unused imports allowed
Comprehensive Hyperliquid Integration Test

This test demonstrates the Hyperliquid exchange integration functionality.
It includes both basic tests and integration examples.
"""

import time
from queue import Queue

from bt_api_py.feeds.live_hyperliquid import HyperliquidRequestDataSpot
from bt_api_py.functions.log_message import SpdLogManager


def test_market_data_queries():
    """Test market data queries that don't require authentication"""

    try:
        # Create data queue and request data instance
        data_queue = Queue()
        request_data = HyperliquidRequestDataSpot(data_queue)

        # Test public endpoints (these should work without authentication)
        result = request_data.get_all_mids()

        result = request_data.get_meta()

        # Test with a specific coin
        result = request_data.get_l2_book("BTC")

        result = request_data.get_recent_trades("BTC", limit=5)

        end_time = int(time.time() * 1000)
        start_time = end_time - 24 * 60 * 60 * 1000  # 24 hours ago
        result = request_data.get_candle_snapshot("BTC", "1h", start_time, end_time)

        result = request_data.get_exchange_status()

        pass

    except Exception:
        pass


def test_authenticated_queries():
    """Test authenticated queries (will fail without proper credentials)"""

    try:
        # Create data queue and request data instance
        data_queue = Queue()

        # Test with mock private key (this will fail but we can catch the error)
        request_data = HyperliquidRequestDataSpot(data_queue, private_key="0x1234567890abcdef")

        # Try to get clearinghouse state (requires valid address)
        try:
            result = request_data.get_clearinghouse_state()
        except ValueError:
            pass

        # Try to place an order (should fail without valid credentials)
        try:
            result = request_data.place_order(
                symbol="BTC", side="buy", quantity=0.001, price=40000, order_type="limit"
            )
        except Exception:
            pass

        pass

    except Exception:
        pass


def test_websocket_subscription():
    """Test WebSocket subscription functionality"""

    try:
        from bt_api_py.feeds.live_hyperliquid import HyperliquidMarketWssDataSpot

        # Create data queue and market WebSocket data instance
        data_queue = Queue()
        wss_data = HyperliquidMarketWssDataSpot(data_queue, symbols=["BTC"])

        # Test subscription methods
        ticker_subscription = wss_data.subscribe_ticker("BTC")

        orderbook_subscription = wss_data.subscribe_orderbook("BTC")

        trades_subscription = wss_data.subscribe_trades("BTC")

        # Test message processing

        # Mock ticker message
        ticker_message = {"channel": "allMids", "data": {"mids": {"BTC": 45000.0, "ETH": 3000.0}}}
        wss_data.process_ticker_message(ticker_message)

        # Mock orderbook message
        orderbook_message = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "levels": [
                    [{"px": 45001, "sz": "0.1", "n": 1}],
                    [{"px": 44999, "sz": "0.2", "n": 1}],
                ],
            },
        }
        wss_data.process_orderbook_message(orderbook_message)

        # Mock trades message
        trades_message = {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "px": "45000.5",
                    "sz": "0.05",
                    "side": "buy",
                    "time": int(time.time() * 1000),
                }
            ],
        }
        wss_data.process_trades_message(trades_message)

        pass

    except Exception:
        pass


def test_config_loading():
    """Test configuration loading from YAML"""

    try:
        from bt_api_py.config_loader import load_exchange_config

        # Load the Hyperliquid config
        config_path = "configs/hyperliquid.yaml"
        config = load_exchange_config(config_path)

        # Check asset types
        for asset_type, asset_config in config.asset_types.items():
            pass

        # Check API endpoints

        pass

    except Exception:
        pass


def main():
    """Run all integration tests"""

    # Set up logging
    logger = SpdLogManager(
        "./logs/test_hyperliquid_integration.log", "test", 0, 0, False
    ).create_logger()

    # Run tests
    tests = [
        ("Market Data Queries", test_market_data_queries),
        ("Authenticated Queries", test_authenticated_queries),
        ("WebSocket Subscriptions", test_websocket_subscription),
        ("Configuration Loading", test_config_loading),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        if test_func():
            passed += 1

    if passed == total:
        pass
    else:
        pass

    return passed == total


if __name__ == "__main__":
    main()
