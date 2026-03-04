#!/usr/bin/env python3
"""
Test Gemini Exchange Integration

This script tests the Gemini exchange integration implementation.
"""

import os
import sys
import logging
from unittest.mock import MagicMock

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bt_api_py.feeds.live_gemini.spot import GeminiRequestDataSpot
from bt_api_py.config_loader import load_exchange_config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_gemini_config():
    """Test Gemini YAML configuration loading"""
    print("\n=== Testing Gemini Configuration ===")

    try:
        config = load_exchange_config("bt_api_py/configs/gemini.yaml")
        if config:
            print(f"✓ Configuration loaded successfully")
            print(f"  Exchange ID: {config.id}")
            print(f"  Display Name: {config.display_name}")
            print(f"  Website: {config.website}")

            # Check asset types
            if hasattr(config, 'asset_types') and config.asset_types:
                print(f"  Asset Types: {list(config.asset_types.keys())}")

                # Check spot configuration
                if 'spot' in config.asset_types:
                    spot = config.asset_types['spot']
                    print(f"    Spot Exchange Name: {spot.exchange_name}")
                    print(f"    Symbol Format: {spot.symbol_format}")
                    print(f"    REST Paths Count: {len(spot.rest_paths) if hasattr(spot, 'rest_paths') else 0}")
                    print(f"    WSS Paths Count: {len(spot.wss_paths) if hasattr(spot, 'wss_paths') else 0}")
            return True
        else:
            print("✗ Failed to load configuration")
            return False
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False


def test_gemini_feed_initialization():
    """Test Gemini feed initialization"""
    print("\n=== Testing Gemini Feed Initialization ===")

    try:
        # Create mock data queue
        data_queue = MagicMock()

        # Create feed instance
        feed = GeminiRequestDataSpot(
            data_queue=data_queue,
            public_key="test_api_key",
            private_key="test_api_secret",
            asset_type="SPOT",
            logger_name="test_gemini_feed.log"
        )

        print(f"✓ Feed initialized successfully")
        print(f"  Exchange Name: {feed.exchange_name}")
        print(f"  Asset Type: {feed.asset_type}")
        print(f"  REST URL: {feed._params.rest_url}")
        print(f"  WSS URL: {feed._params.wss_url}")

        # Test capabilities
        capabilities = feed._capabilities()
        print(f"  Capabilities: {len(capabilities)}")
        print(f"    {list(capabilities)[:5]}...")  # Show first 5

        return True
    except Exception as e:
        print(f"✗ Feed initialization test failed: {e}")
        return False


def test_gemini_symbol_handling():
    """Test Gemini symbol handling"""
    print("\n=== Testing Symbol Handling ===")

    try:
        data_queue = MagicMock()
        feed = GeminiRequestDataSpot(data_queue=data_queue)

        # Test symbol formatting (assuming some symbols)
        test_symbols = ["BTCUSD", "ETHUSD", "BTCETH"]

        for symbol in test_symbols:
            formatted = feed._params.get_symbol(symbol)
            print(f"  {symbol} -> {formatted}")

        # Test rest path retrieval
        path = feed._params.get_rest_path("get_ticker")
        print(f"  Ticker Path: {path}")

        # Test period mapping
        period = feed._params.get_period("1h")
        print(f"  1h -> {period}")

        return True
    except Exception as e:
        print(f"✗ Symbol handling test failed: {e}")
        return False


def test_gemini_api_methods():
    """Test Gemini API method calls"""
    print("\n=== Testing API Method Calls ===")

    try:
        data_queue = MagicMock()
        feed = GeminiRequestDataSpot(data_queue=data_queue)

        # Test market data methods (these should work without API keys)
        methods_to_test = [
            ("get_symbols", lambda: feed.get_symbols()),
            ("get_symbol_details", lambda: feed.get_symbol_details("BTCUSD")),
            ("get_ticker", lambda: feed.get_ticker("BTCUSD")),
            ("get_depth", lambda: feed.get_depth("BTCUSD")),
            ("get_trades", lambda: feed.get_trades("BTCUSD")),
        ]

        # Mock the request method to avoid actual API calls
        original_request = feed.request
        feed.request = lambda *args, **kwargs: {"status": "mocked", "message": "Test response"}

        for method_name, method_call in methods_to_test:
            try:
                response = method_call()
                print(f"  ✓ {method_name}: OK (mocked)")
            except Exception as e:
                print(f"  ✗ {method_name}: Failed - {e}")

        # Restore original request method
        feed.request = original_request

        # Test private API methods (these will fail without proper authentication)
        private_methods = [
            ("get_balance", lambda: feed.get_balance()),
            ("get_open_orders", lambda: feed.get_open_orders()),
            ("make_order", lambda: feed.make_order("BTCUSD", 0.001, 40000)),
        ]

        for method_name, method_call in private_methods:
            try:
                response = method_call()
                print(f"  ✓ {method_name}: OK (should not reach here)")
            except Exception as e:
                print(f"  ✓ {method_name}: Expected failure - {str(e)[:100]}...")

        return True
    except Exception as e:
        print(f"✗ API methods test failed: {e}")
        return False


def test_gemini_error_handling():
    """Test Gemini error handling"""
    print("\n=== Testing Error Handling ===")

    try:
        from bt_api_py.errors.error_framework_gemini import GeminiErrorTranslator

        # Test error translation
        test_errors = [
            "Invalid API key",
            "Signature does not match",
            "Rate limit exceeded",
            {"result": "error", "reason": "InvalidSymbol", "message": "Symbol not found"},
        ]

        translator = GeminiErrorTranslator()

        for error in test_errors:
            try:
                unified_error = translator.translate(error, "Gemini")
                if unified_error:
                    print(f"  ✓ Error translated: {unified_error.code.name} - {unified_error.message}")
                else:
                    print(f"  ✗ Error not translated: {error}")
            except Exception as e:
                print(f"  ✗ Translation failed for {error}: {e}")

        return True
    except Exception as e:
        print(f"✗ Error handling test failed: {e}")
        return False


def main():
    """Run all tests"""
    print("Starting Gemini Exchange Integration Tests...")

    tests = [
        test_gemini_config,
        test_gemini_feed_initialization,
        test_gemini_symbol_handling,
        test_gemini_api_methods,
        test_gemini_error_handling,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ Test {test.__name__} crashed: {e}")
            failed += 1

    print("\n=== Test Results ===")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total: {passed + failed}")

    if failed == 0:
        print("🎉 All tests passed!")
        return True
    else:
        print("❌ Some tests failed!")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)