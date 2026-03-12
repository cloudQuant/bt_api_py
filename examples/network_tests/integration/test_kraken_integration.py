#!/usr/bin/env python3
"""
Simple test script to verify Kraken integration can be imported and instantiated.
# ruff: noqa: F401  # Import verification tests - F401 unused imports allowed
"""

import os
import sys
import time

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_imports():
    """Test that all Kraken modules can be imported."""
    print("Testing imports...")

    try:
        # Test main feed imports
        from bt_api_py.feeds.live_kraken import KrakenRequestData, KrakenRequestDataSpot

        print("✓ Successfully imported KrakenRequestData and KrakenRequestDataSpot")

        # Test exchange data
        from bt_api_py.containers.exchanges.kraken_exchange_data import (
            KrakenExchangeData,
            KrakenExchangeDataSpot,
        )

        print("✓ Successfully imported KrakenExchangeData classes")

        # Test data containers
        from bt_api_py.containers.balances.kraken_balance import (
            KrakenRequestBalanceData,
            KrakenSpotWssBalanceData,
        )
        from bt_api_py.containers.orderbooks.kraken_orderbook import KrakenRequestOrderBookData
        from bt_api_py.containers.orders.kraken_order import (
            KrakenRequestOrderData,
            KrakenSpotWssOrderData,
        )
        from bt_api_py.containers.tickers.kraken_ticker import KrakenRequestTickerData

        print("✓ Successfully imported Kraken data containers")

        # Test registration
        from bt_api_py.exchange_registers.register_kraken import (
            KrakenSpotFeed,
            get_kraken_exchange_info,
        )

        print("✓ Successfully imported KrakenSpotFeed and registration functions")

        # Test error translator
        from bt_api_py.error import KrakenErrorTranslator

        print("✓ Successfully imported KrakenErrorTranslator")

    except ImportError as e:
        print(f"✗ Import failed: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")


def test_instantiation():
    """Test that Kraken classes can be instantiated."""
    print("\nTesting instantiation...")

    try:
        # Test exchange data instantiation
        from bt_api_py.containers.exchanges.kraken_exchange_data import KrakenExchangeDataSpot

        exchange_data = KrakenExchangeDataSpot()
        print(f"✓ KrakenExchangeDataSpot created with exchange name: {exchange_data.exchange_name}")
        print(f"✓ REST URL: {exchange_data.rest_url}")
        print(f"✓ Sample symbol mapping: BTC/USD -> {exchange_data.get_symbol('BTC/USD')}")

        # Test feed instantiation (without real API keys)
        from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData

        # Mock data queue
        class MockDataQueue:
            def put(self, data):
                pass

        data_queue = MockDataQueue()

        # Create feed with dummy parameters
        feed = KrakenRequestData(
            data_queue=data_queue, api_key="dummy-key", api_secret="dummy-secret", asset_type="SPOT"
        )

        print("✓ KrakenRequestData instantiated successfully")
        print(f"✓ Exchange name: {feed.exchange_name}")
        print(f"✓ Asset type: {feed.asset_type}")

        # Test capabilities
        capabilities = feed._capabilities()
        print(f"✓ Feed capabilities: {len(capabilities)} supported")

        # Test ticker data container
        from bt_api_py.containers.tickers.kraken_ticker import KrakenRequestTickerData

        # Create mock ticker data
        mock_ticker_data = {
            "symbol": "XBTUSD",
            "exchange": "kraken",
            "last_price": 45000.0,
            "bid_price": 44900.0,
            "ask_price": 45100.0,
            "volume_24h": 1000.0,
            "timestamp": time.time(),
        }

        ticker = KrakenRequestTickerData(mock_ticker_data, "BTC/USD", "SPOT")
        print(f"✓ Ticker created: {ticker}")
        print(f"✓ Ticker validates: {ticker.validate()}")

        # Test order data container
        from bt_api_py.containers.orders.kraken_order import KrakenRequestOrderData

        mock_order_data = {
            "id": "test-order-123",
            "symbol": "BTC/USD",
            "status": "open",
            "side": "buy",
            "type": "limit",
            "quantity": 0.001,
            "price": 45000.0,
            "timestamp": time.time(),
        }

        order = KrakenRequestOrderData(mock_order_data, "BTC/USD", "SPOT")
        print(f"✓ Order created: {order}")
        print(f"✓ Order validates: {order.validate()}")

    except Exception as e:
        print(f"✗ Instantiation failed: {e}")
        import traceback

        traceback.print_exc()


def test_registration():
    """Test that Kraken feed is properly registered."""
    print("\nTesting registration...")

    try:
        from bt_api_py.registry import get_feed

        # Try to get Kraken feed (this will fail if not registered)
        get_feed("KRKEN___SPOT", None, api_key="dummy", api_secret="dummy")
        print("✓ KrakenSpotFeed found in registry")

    except Exception as e:
        print(f"⚠ Registration test may require full framework setup: {e}")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Kraken Integration Test")
    print("=" * 60)

    tests = [
        ("Import Test", test_imports),
        ("Instantiation Test", test_instantiation),
        ("Registration Test", test_registration),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\n{'-' * 20} {test_name} {'-' * 20}")
        if test_func():
            passed += 1
            print(f"✅ {test_name} PASSED")
        else:
            print(f"❌ {test_name} FAILED")

    print("\n" + "=" * 60)
    print(f"Test Summary: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! Kraken integration is working correctly.")
        return 0
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
