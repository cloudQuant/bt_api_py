#!/usr/bin/env python3
"""
Simple test script to verify Bitget integration
"""

import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queue import Queue
from bt_api_py.feeds.live_bitget import BitgetRequestData, BitgetRequestDataSpot
from bt_api_py.containers.exchanges.bitget_exchange_data import BitgetExchangeData, BitgetExchangeDataSpot


def test_bitget_data_imports():
    """Test Bitget data containers can be imported"""
    try:
        from bt_api_py.containers.tickers.bitget_ticker import BitgetTickerData
        from bt_api_py.containers.orders.bitget_order import BitgetOrderData
        from bt_api_py.containers.orderbooks.bitget_orderbook import BitgetOrderBookData
        from bt_api_py.containers.balances.bitget_balance import BitgetBalanceData
        from bt_api_py.containers.trades.bitget_trade import BitgetTradeData
        from bt_api_py.containers.accounts.bitget_account import BitgetSpotWssAccountData
        print("✓ All Bitget data containers imported successfully")
        return True
    except Exception as e:
        print(f"✗ Error importing Bitget data containers: {e}")
        return False


def test_bitget_feed_imports():
    """Test Bitget feed classes can be imported"""
    try:
        from bt_api_py.feeds.live_bitget import BitgetRequestData, BitgetRequestDataSpot
        print("✓ Bitget feed classes imported successfully")
        return True
    except Exception as e:
        print(f"✗ Error importing Bitget feed classes: {e}")
        return False


def test_bitget_exchange_data():
    """Test Bitget exchange data configuration"""
    try:
        # Test base class
        exchange_data = BitgetExchangeData()
        assert exchange_data.exchange_name == "bitget"
        assert exchange_data.rest_url == "https://api.bitget.com"
        print("✓ BitgetExchangeData base class initialized successfully")

        # Test spot class
        spot_data = BitgetExchangeDataSpot()
        assert spot_data.exchange_name == "bitget"
        assert spot_data.rest_url == "https://api.bitget.com"
        print("✓ BitgetExchangeDataSpot class initialized successfully")

        # Test symbol conversion
        assert spot_data.get_symbol("BTC-USDT") == "BTCUSDT"
        assert spot_data.get_symbol("ETH-USDT") == "ETHUSDT"
        print("✓ Symbol conversion working correctly")

        return True
    except Exception as e:
        print(f"✗ Error testing Bitget exchange data: {e}")
        return False


def test_bitget_feed_instantiation():
    """Test Bitget feed classes can be instantiated"""
    try:
        data_queue = Queue()

        # Test base feed
        feed = BitgetRequestData(data_queue, exchange_name="bitget", public_key="test_key", private_key="test_secret", passphrase="test_pass")
        print(f"Feed exchange_name: {feed.exchange_name}")
        assert feed.exchange_name == "bitget", f"Expected 'bitget', got '{feed.exchange_name}'"
        assert feed.public_key == "test_key"
        assert feed.private_key == "test_secret"
        assert feed.passphrase == "test_pass"
        print("✓ BitgetRequestData instantiated successfully")

        # Test spot feed
        spot_feed = BitgetRequestDataSpot(data_queue, exchange_name="bitget", public_key="test_key", private_key="test_secret", passphrase="test_pass")
        assert spot_feed.exchange_name == "bitget"
        assert spot_feed.asset_type == "SPOT"
        print("✓ BitgetRequestDataSpot instantiated successfully")

        return True
    except Exception as e:
        print(f"✗ Error instantiating Bitget feeds: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_bitget_registration():
    """Test Bitget registration module"""
    try:
        from bt_api_py.exchange_registers.register_bitget import register_bitget, _bitget_spot_subscribe_handler
        print("✓ Bitget registration module imported successfully")

        # Test registration function exists
        assert callable(register_bitget)
        assert callable(_bitget_spot_subscribe_handler)
        print("✓ Bitget registration functions are callable")

        return True
    except Exception as e:
        print(f"✗ Error testing Bitget registration: {e}")
        return False


def main():
    """Run all tests"""
    print("Testing Bitget Integration...")
    print("=" * 50)

    tests = [
        ("Bitget Data Imports", test_bitget_data_imports),
        ("Bitget Feed Imports", test_bitget_feed_imports),
        ("Bitget Exchange Data", test_bitget_exchange_data),
        ("Bitget Feed Instantiation", test_bitget_feed_instantiation),
        ("Bitget Registration", test_bitget_registration),
    ]

    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        result = test_func()
        results.append((test_name, result))

    print("\n" + "=" * 50)
    print("Test Results:")
    all_passed = True
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
        if not result:
            all_passed = False

    if all_passed:
        print("\n🎉 All tests passed! Bitget integration is working correctly.")
        return 0
    else:
        print("\n❌ Some tests failed. Please check the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())