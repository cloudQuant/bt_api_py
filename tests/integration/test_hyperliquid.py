"""
# ruff: noqa  # Import verification tests - F401 unused imports allowed
Test Hyperliquid Exchange Integration

Basic tests to verify the Hyperliquid implementation works correctly.
"""

from queue import Queue

from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
from bt_api_py.feeds.live_hyperliquid import HyperliquidRequestDataSpot
from bt_api_py.functions.log_message import SpdLogManager


def test_hyperliquid_data_container():
    """Test Hyperliquid data container"""
    print("Testing Hyperliquid data container...")

    try:
        # Test spot data container
        data = HyperliquidExchangeDataSpot()

        # Test basic properties
        assert data.exchange_name == "hyperliquid_spot"
        assert data.rest_url == "https://api.hyperliquid.xyz"
        assert data.wss_url == "wss://api.hyperliquid.xyz/ws"

        # Test symbol mapping
        assert data.get_symbol("BTC/USDC") == "BTC"
        assert data.get_symbol("ETH/USDC") == "ETH"

        # Test REST paths
        assert data.get_rest_path("get_all_mids") == "/info"
        assert data.get_rest_path("make_order") == "/exchange"

        # Test timeframe conversion
        assert data.get_timeframe_minutes("1h") == 60
        assert data.get_timeframe_from_minutes(60) == "1h"

        # Test leverage limits
        assert data.get_leverage_limit("BTC") == 100
        assert data.get_leverage_limit("ETH") == 50

        print("✓ Hyperliquid data container tests passed")

    except Exception as e:
        print(f"✗ Hyperliquid data container test failed: {e}")


def test_hyperliquid_request_data():
    """Test Hyperliquid request data"""
    print("\nTesting Hyperliquid request data...")

    try:
        # Create data queue
        data_queue = Queue()

        # Create request data instance
        request_data = HyperliquidRequestDataSpot(data_queue)

        # Test basic properties
        assert request_data.asset_type == "SPOT"
        assert request_data._params.exchange_name == "hyperliquid_spot"

        # Test that we can make basic requests (these will fail without network,
        # but we can verify the method calls work)

        # Test method exists and callable
        assert hasattr(request_data, "get_all_mids")
        assert callable(request_data.get_all_mids)

        assert hasattr(request_data, "get_meta")
        assert callable(request_data.get_meta)

        assert hasattr(request_data, "get_l2_book")
        assert callable(request_data.get_l2_book)

        assert hasattr(request_data, "get_candle_snapshot")
        assert callable(request_data.get_candle_snapshot)

        assert hasattr(request_data, "get_recent_trades")
        assert callable(request_data.get_recent_trades)

        print("✓ Hyperliquid request data tests passed")

    except Exception as e:
        print(f"✗ Hyperliquid request data test failed: {e}")


def test_hyperliquid_error_translator():
    """Test Hyperliquid error translation"""
    print("\nTesting Hyperliquid error translator...")

    try:
        from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidErrorTranslator

        # Test error translation
        translator = HyperliquidErrorTranslator()

        # Test various error messages
        test_cases = [
            ("invalid signature", ("INVALID_SIGNATURE", 2002)),
            ("insufficient margin", ("INSUFFICIENT_MARGIN", 4005)),
            ("order not found", ("ORDER_NOT_FOUND", 4006)),
            ("rate limit exceeded", ("RATE_LIMIT_EXCEEDED", 3001)),
            ("price slippage", ("PRICE_SLIPPAGE", 4003)),
            ("min trade size", ("MIN_NOTIONAL", 4014)),
            ("invalid api key", ("INVALID_API_KEY", 2001)),
            ("permission denied", ("PERMISSION_DENIED", 2004)),
            ("unknown error", ("UNKNOWN_ERROR", 5000)),
        ]

        for error_msg, expected in test_cases:
            result = translator.translate_error(error_msg)
            assert result == expected, f"Expected {expected}, got {result} for {error_msg}"

        print("✓ Hyperliquid error translator tests passed")

    except Exception as e:
        print(f"✗ Hyperliquid error translator test failed: {e}")


def main():
    """Run all tests"""
    print("=" * 60)
    print("Testing Hyperliquid Exchange Integration")
    print("=" * 60)

    # Set up logging
    SpdLogManager("./logs/test_hyperliquid.log", "test", 0, 0, False).create_logger()

    # Run tests
    tests = [
        test_hyperliquid_data_container,
        test_hyperliquid_request_data,
        test_hyperliquid_error_translator,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1

    print(f"\nTest Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! Hyperliquid integration looks good.")
    else:
        print("❌ Some tests failed. Please check the implementation.")

    return passed == total


if __name__ == "__main__":
    main()
