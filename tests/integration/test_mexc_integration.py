#!/usr/bin/env python3
"""
Test script for MEXC integration
# ruff: noqa: F401  # Import verification tests - F401 unused imports allowed

This script tests whether the MEXC feed can be imported and instantiated.
"""

import sys
import os
from queue import Queue

# Add the project root to the path
sys.path.insert(0, '/Users/yunjinqi/Documents/source_code/bt_api_py')

def test_mexc_import():
    """Test if MEXC modules can be imported"""
    try:
        from bt_api_py.feeds.live_mexc import MexcRequestData, MexcRequestDataSpot
        print("✓ Successfully imported MEXC feed classes")
        pass
    except ImportError as e:
        print(f"✗ Failed to import MEXC feed classes: {e}")
        pass

def test_mexc_exchange_data():
    """Test if MEXC exchange data can be imported and instantiated"""
    try:
        from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot

        # Create an instance
        exchange_data = MexcExchangeDataSpot()
        print(f"✓ Successfully created MexcExchangeDataSpot instance")
        print(f"  - Exchange name: {exchange_data.exchange_name}")
        print(f"  - REST URL: {exchange_data.rest_url}")
        print(f"  - WebSocket URL: {exchange_data.wss_url}")
        print(f"  - Number of REST paths: {len(exchange_data.rest_paths)}")
        print(f"  - Number of WebSocket paths: {len(exchange_data.wss_paths)}")
        pass
    except Exception as e:
        print(f"✗ Failed to create MexcExchangeDataSpot instance: {e}")
        pass

def test_mexc_data_containers():
    """Test if MEXC data containers can be imported"""
    try:
        from bt_api_py.containers.tickers.mexc_ticker import MexcRequestTickerData
        from bt_api_py.containers.orders.mexc_order import MexcRequestOrderData
        from bt_api_py.containers.orderbooks.mexc_orderbook import MexcRequestOrderBookData
        from bt_api_py.containers.balances.mexc_balance import MexcRequestBalanceData
        from bt_api_py.containers.trades.mexc_trade import MexcRequestTradeData

        print("✓ Successfully imported all MEXC data container classes")
        pass
    except ImportError as e:
        print(f"✗ Failed to import MEXC data container classes: {e}")
        pass

def test_mexc_registration():
    """Test if MEXC registration works"""
    try:
        # This should register the feed without errors
        import bt_api_py.exchange_registers.register_mexc
        print("✓ Successfully imported MEXC registration module")
        pass
    except Exception as e:
        print(f"✗ Failed to import MEXC registration module: {e}")
        pass

def test_mexc_feed_instantiation():
    """Test if MEXC feed can be instantiated"""
    try:
        from bt_api_py.feeds.live_mexc import MexcRequestDataSpot
        from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
        from queue import Queue

        # Create a data queue
        data_queue = Queue()

        # Create exchange data
        exchange_data = MexcExchangeDataSpot()

        # Create feed instance (without authentication for testing)
        feed = MexcRequestDataSpot(
            data_queue=data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_data=exchange_data
        )

        print("✓ Successfully created MexcRequestDataSpot instance")
        print(f"  - Asset type: {feed.asset_type}")
        print(f"  - Exchange name: {feed.exchange_name}")
        pass
    except Exception as e:
        print(f"✗ Failed to create MexcRequestDataSpot instance: {e}")
        pass

def main():
    """Run all tests"""
    print("Testing MEXC integration...")
    print("=" * 50)

    tests = [
        ("Import MEXC feed classes", test_mexc_import),
        ("Create MEXC exchange data", test_mexc_exchange_data),
        ("Import MEXC data containers", test_mexc_data_containers),
        ("Test MEXC registration", test_mexc_registration),
        ("Instantiate MEXC feed", test_mexc_feed_instantiation),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\nRunning test: {test_name}")
        if test_func():
            passed += 1

    print("\n" + "=" * 50)
    print(f"Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! MEXC integration is working correctly.")
        pass
    else:
        print("❌ Some tests failed. Please check the implementation.")
        pass

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)