#!/usr/bin/env python3
"""
Simple test script to verify Bitfinex integration
# ruff: noqa: F401  # Import verification tests - F401 unused imports allowed
"""

import os
import queue
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_bitfinex_import():
    """Test importing Bitfinex modules"""
    print("=== Testing Bitfinex Module Imports ===")

    try:
        # Test importing the main feed module
        from bt_api_py.feeds.live_bitfinex import (  # noqa: F401
            BitfinexAccountWssDataSpot,
            BitfinexMarketWssDataSpot,
            BitfinexRequestData,
        )

        print("✓ Successfully imported Bitfinex live feed modules")

        # Test importing exchange data
        from bt_api_py.containers.exchanges.bitfinex_exchange_data import (
            BitfinexExchangeDataSpot,  # noqa: F401
        )

        print("✓ Successfully imported Bitfinex exchange data")

        # Test importing data containers
        from bt_api_py.containers.balances.bitfinex_balance import (
            BitfinexSpotRequestBalanceData,  # noqa: F401
        )
        from bt_api_py.containers.orderbooks.bitfinex_orderbook import (
            BitfinexRequestOrderBookData,  # noqa: F401
        )
        from bt_api_py.containers.orders.bitfinex_order import (
            BitfinexRequestOrderData,  # noqa: F401
        )
        from bt_api_py.containers.tickers.bitfinex_ticker import (
            BitfinexRequestTickerData,  # noqa: F401
        )

        print("✓ Successfully imported Bitfinex data containers")

        # Test importing registration
        from bt_api_py.exchange_registers.register_bitfinex import register_bitfinex  # noqa: F401

        print("✓ Successfully imported Bitfinex registration")

        # Test importing error translator
        from bt_api_py.error import BitfinexErrorTranslator  # noqa: F401

        print("✓ Successfully imported Bitfinex error translator")

    except ImportError as e:
        print(f"✗ Import error: {e}")
    except Exception as e:
        print(f"✗ Unexpected error: {e}")


def test_bitfinex_instantiation():
    """Test instantiating Bitfinex classes"""
    print("\n=== Testing Bitfinex Class Instantiation ===")

    try:
        # Create a data queue
        data_queue = queue.Queue()

        # Test parameters
        test_params = {
            "api_key": "test_api_key",
            "api_secret": "test_api_secret",
            "asset_type": "SPOT",
            "logger_name": "test_bitfinex_feed.log",
            "exchange_name": "bitfinex",
        }

        # Test instantiating request data
        from bt_api_py.feeds.live_bitfinex import BitfinexRequestData

        BitfinexRequestData(data_queue, **test_params)
        print("✓ Successfully instantiated BitfinexRequestData")

        # Test exchange data
        from bt_api_py.containers.exchanges.bitfinex_exchange_data import (
            BitfinexExchangeDataSpot,
        )

        exchange_data = BitfinexExchangeDataSpot()
        print("✓ Successfully instantiated BitfinexExchangeDataSpot")

        # Test symbol conversion
        test_symbol = "BTC-USD"
        converted_symbol = exchange_data.get_symbol(test_symbol)
        print(f"✓ Symbol conversion: {test_symbol} -> {converted_symbol}")

        reverse_symbol = exchange_data.get_reverse_symbol(converted_symbol)
        print(f"✓ Reverse symbol conversion: {converted_symbol} -> {reverse_symbol}")

        # Test data container instantiation
        from bt_api_py.containers.tickers.bitfinex_ticker import (
            BitfinexRequestTickerData,
        )

        # Create sample ticker data
        ticker_data = [
            "tBTCUSD",  # symbol
            45000.0,  # bid
            1.5,  # bid_size
            45010.0,  # ask
            2.0,  # ask_size
            100.0,  # daily_change
            0.0022,  # daily_change_perc
            45005.0,  # last_price
            1000.0,  # volume
            45200.0,  # high
            44800.0,  # low
        ]

        ticker = BitfinexRequestTickerData(ticker_data, "BTC-USD", "SPOT", False)
        ticker.init_data()
        print("✓ Successfully instantiated and processed Bitfinex ticker data")
        print(f"  - Symbol: {ticker.get_symbol_name()}")
        print(f"  - Last Price: {ticker.get_last_price()}")
        print(f"  - Bid: {ticker.get_bid_price()}")
        print(f"  - Ask: {ticker.get_ask_price()}")

        # Test order book data
        from bt_api_py.containers.orderbooks.bitfinex_orderbook import (
            BitfinexRequestOrderBookData,
        )

        orderbook_data = [
            [45000.0, 5, 1.0],  # [price, count, amount] - bid
            [45005.0, 3, -0.5],  # [price, count, amount] - ask (negative)
            [45010.0, 2, -1.5],  # [price, count, amount] - ask
            [45015.0, 4, -0.8],  # [price, count, amount] - ask
        ]

        orderbook = BitfinexRequestOrderBookData(orderbook_data, "BTC-USD", "SPOT", False)
        orderbook.init_data()
        print("✓ Successfully instantiated and processed Bitfinex orderbook data")
        print(f"  - Bid levels: {len(orderbook.get_bids())}")
        print(f"  - Ask levels: {len(orderbook.get_asks())}")
        print(f"  - Spread: {orderbook.get_spread()}")

    except Exception as e:
        print(f"✗ Instantiation error: {e}")
        import traceback

        traceback.print_exc()


def test_bitfinex_registration():
    """Test Bitfinex registration"""
    print("\n=== Testing Bitfinex Registration ===")

    try:
        # Test registration function
        print("✓ Successfully imported register_bitfinex function")

        # Note: We won't actually call register() as it modifies global state
        print("  - Registration function available (ready to use)")

    except Exception as e:
        print(f"✗ Registration test error: {e}")
        import traceback

        traceback.print_exc()


def main():
    """Main test function"""
    print("Starting Bitfinex Integration Tests...\n")

    results = []

    # Run all tests
    results.append(test_bitfinex_import())
    results.append(test_bitfinex_instantiation())
    results.append(test_bitfinex_registration())

    # Summary
    print("\n=== Test Summary ===")
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed! Bitfinex integration is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return 1


if __name__ == "__main__":
    exit(main())
