#!/usr/bin/env python3
"""
Simple test script to verify Bitget integration
"""

import os
import sys

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queue import Queue

from bt_api_py.containers.exchanges.bitget_exchange_data import (
    BitgetExchangeData,
    BitgetExchangeDataSpot,
)
from bt_api_py.feeds.live_bitget import BitgetRequestData, BitgetRequestDataSpot


def test_bitget_data_imports():
    """Test Bitget data containers can be imported"""
    print("✓ All Bitget data containers imported successfully")


def test_bitget_feed_imports():
    """Test Bitget feed classes can be imported"""
    print("✓ Bitget feed classes imported successfully")


def test_bitget_exchange_data():
    """Test Bitget exchange data configuration"""
    # Test base class (loads spot config by default)
    exchange_data = BitgetExchangeData()
    assert exchange_data.exchange_name in ("bitget", "bitgetSpot", "BITGET")  # config-driven
    assert exchange_data.rest_url == "https://api.bitget.com"
    print("✓ BitgetExchangeData base class initialized successfully")

    # Test spot class
    spot_data = BitgetExchangeDataSpot()
    assert spot_data.exchange_name == "bitgetSpot"
    assert spot_data.rest_url == "https://api.bitget.com"
    print("✓ BitgetExchangeDataSpot class initialized successfully")

    # Test symbol conversion
    assert spot_data.get_symbol("BTC-USDT") == "BTCUSDT"
    assert spot_data.get_symbol("ETH-USDT") == "ETHUSDT"
    print("✓ Symbol conversion working correctly")


def test_bitget_feed_instantiation():
    """Test Bitget feed classes can be instantiated"""
    data_queue = Queue()

    # Test base feed
    feed = BitgetRequestData(
        data_queue,
        exchange_name="bitget",
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_pass",
    )
    print(f"Feed exchange_name: {feed.exchange_name}")
    assert feed.exchange_name == "bitget", f"Expected 'bitget', got '{feed.exchange_name}'"
    assert feed.public_key == "test_key"
    assert feed.private_key == "test_secret"
    assert feed.passphrase == "test_pass"
    print("✓ BitgetRequestData instantiated successfully")

    # Test spot feed
    spot_feed = BitgetRequestDataSpot(
        data_queue,
        exchange_name="bitget",
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_pass",
    )
    assert spot_feed.exchange_name == "bitget"
    assert spot_feed.asset_type == "spot"
    print("✓ BitgetRequestDataSpot instantiated successfully")


def test_bitget_registration():
    """Test Bitget registration module"""
    from bt_api_py.exchange_registers.register_bitget import (
        _bitget_spot_subscribe_handler,
        register_bitget,
    )

    print("✓ Bitget registration module imported successfully")

    # Test registration function exists
    assert callable(register_bitget)
    assert callable(_bitget_spot_subscribe_handler)
    print("✓ Bitget registration functions are callable")


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
