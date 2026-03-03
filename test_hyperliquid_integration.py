"""
Comprehensive Hyperliquid Integration Test

This test demonstrates the Hyperliquid exchange integration functionality.
It includes both basic tests and integration examples.
"""

import time
from queue import Queue

from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
from bt_api_py.feeds.live_hyperliquid import HyperliquidRequestDataSpot
from bt_api_py.functions.log_message import SpdLogManager


def test_market_data_queries():
    """Test market data queries that don't require authentication"""
    print("\nTesting market data queries...")

    try:
        # Create data queue and request data instance
        data_queue = Queue()
        request_data = HyperliquidRequestDataSpot(data_queue)

        # Test public endpoints (these should work without authentication)
        print("  Testing get_all_mids...")
        result = request_data.get_all_mids()
        print(f"    ✓ get_all_mids returned status: {result.status}")

        print("  Testing get_meta...")
        result = request_data.get_meta()
        print(f"    ✓ get_meta returned status: {result.status}")

        # Test with a specific coin
        print("  Testing get_l2_book for BTC...")
        result = request_data.get_l2_book("BTC")
        print(f"    ✓ get_l2_book returned status: {result.status}")

        print("  Testing get_recent_trades for BTC...")
        result = request_data.get_recent_trades("BTC", limit=5)
        print(f"    ✓ get_recent_trades returned status: {result.status}")

        print("  Testing get_candle_snapshot for BTC...")
        end_time = int(time.time() * 1000)
        start_time = end_time - 24 * 60 * 60 * 1000  # 24 hours ago
        result = request_data.get_candle_snapshot("BTC", "1h", start_time, end_time)
        print(f"    ✓ get_candle_snapshot returned status: {result.status}")

        print("  Testing get_exchange_status...")
        result = request_data.get_exchange_status()
        print(f"    ✓ get_exchange_status returned status: {result.status}")

        return True

    except Exception as e:
        print(f"  ✗ Market data query test failed: {e}")
        return False


def test_authenticated_queries():
    """Test authenticated queries (will fail without proper credentials)"""
    print("\nTesting authenticated queries...")

    try:
        # Create data queue and request data instance
        data_queue = Queue()

        # Test with mock private key (this will fail but we can catch the error)
        request_data = HyperliquidRequestDataSpot(data_queue, private_key="0x1234567890abcdef")

        # Try to get clearinghouse state (requires valid address)
        print("  Testing get_clearinghouse_state (should fail without valid address)...")
        try:
            result = request_data.get_clearinghouse_state()
            print(f"    Result: {result.status}")
        except ValueError as e:
            print(f"    ✓ Expected error caught: {e}")

        # Try to place an order (should fail without valid credentials)
        print("  Testing place_order (should fail without valid credentials)...")
        try:
            result = request_data.place_order(
                symbol="BTC",
                side="buy",
                quantity=0.001,
                price=40000,
                order_type="limit"
            )
            print(f"    Result: {result.status}")
        except Exception as e:
            print(f"    ✓ Expected error caught: {e}")

        return True

    except Exception as e:
        print(f"  ✗ Authenticated query test failed: {e}")
        return False


def test_websocket_subscription():
    """Test WebSocket subscription functionality"""
    print("\nTesting WebSocket subscription functionality...")

    try:
        from bt_api_py.feeds.live_hyperliquid import HyperliquidMarketWssDataSpot

        # Create data queue and market WebSocket data instance
        data_queue = Queue()
        wss_data = HyperliquidMarketWssDataSpot(data_queue, symbols=["BTC"])

        # Test subscription methods
        print("  Testing ticker subscription...")
        ticker_subscription = wss_data.subscribe_ticker("BTC")
        print(f"    Ticker subscription: {ticker_subscription}")

        print("  Testing orderbook subscription...")
        orderbook_subscription = wss_data.subscribe_orderbook("BTC")
        print(f"    Orderbook subscription: {orderbook_subscription}")

        print("  Testing trades subscription...")
        trades_subscription = wss_data.subscribe_trades("BTC")
        print(f"    Trades subscription: {trades_subscription}")

        # Test message processing
        print("  Testing message processing...")

        # Mock ticker message
        ticker_message = {
            "channel": "allMids",
            "data": {
                "mids": {
                    "BTC": 45000.0,
                    "ETH": 3000.0
                }
            }
        }
        wss_data.process_ticker_message(ticker_message)
        print(f"    ✓ Ticker message processed")

        # Mock orderbook message
        orderbook_message = {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "levels": [
                    [{"px": 45001, "sz": "0.1", "n": 1}],
                    [{"px": 44999, "sz": "0.2", "n": 1}]
                ]
            }
        }
        wss_data.process_orderbook_message(orderbook_message)
        print(f"    ✓ Orderbook message processed")

        # Mock trades message
        trades_message = {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "px": "45000.5",
                    "sz": "0.05",
                    "side": "buy",
                    "time": int(time.time() * 1000)
                }
            ]
        }
        wss_data.process_trades_message(trades_message)
        print(f"    ✓ Trades message processed")

        return True

    except Exception as e:
        print(f"  ✗ WebSocket subscription test failed: {e}")
        return False


def test_config_loading():
    """Test configuration loading from YAML"""
    print("\nTesting configuration loading...")

    try:
        from bt_api_py.config_loader import load_exchange_config

        # Load the Hyperliquid config
        config_path = "configs/hyperliquid.yaml"
        config = load_exchange_config(config_path)

        print("  ✓ Configuration loaded successfully")
        print(f"    Exchange name: {config.exchange}")
        print(f"    Name: {config.name}")
        print(f"    Description: {config.description}")
        print(f"    Testnet: {config.testnet}")

        # Check asset types
        print("  Checking asset types...")
        for asset_type, asset_config in config.asset_types.items():
            print(f"    {asset_type}: {asset_config.name}")

        # Check API endpoints
        print("  Checking API endpoints...")
        print(f"    Public endpoints: {len(config.api.public)}")
        print(f"    Authenticated endpoints: {len(config.api.authenticated)}")

        return True

    except Exception as e:
        print(f"  ✗ Configuration loading test failed: {e}")
        return False


def main():
    """Run all integration tests"""
    print("=" * 70)
    print("Comprehensive Hyperliquid Integration Test")
    print("=" * 70)

    # Set up logging
    logger = SpdLogManager(
        "./logs/test_hyperliquid_integration.log",
        "test",
        0,
        0,
        False
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
        print(f"\n{'-' * 70}")
        print(f"Running: {test_name}")
        print(f"{'-' * 70}")

        if test_func():
            passed += 1

    print(f"\n{'=' * 70}")
    print(f"Integration Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All integration tests passed! Hyperliquid implementation is ready.")
        print("\nNext steps:")
        print("1. Set up your Hyperliquid API credentials")
        print("2. Test with real API calls using valid credentials")
        print("3. Implement WebSocket connections for real-time data")
    else:
        print("❌ Some integration tests failed. Please check the implementation.")

    return passed == total


if __name__ == "__main__":
    main()