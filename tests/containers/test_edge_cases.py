"""
Edge case and boundary value tests for data containers
Target: 50+ tests for edge cases
"""

import json

import pytest

from bt_api_py.containers.balances.binance_balance import BinanceWssBalanceData
from bt_api_py.containers.orders.binance_order import BinanceForceOrderData
from bt_api_py.containers.tickers.binance_ticker import (
    BinanceRequestTickerData,
    BinanceWssTickerData,
)


class TestTickerEdgeCases:
    """Test edge cases for ticker data containers"""

    def test_ticker_zero_prices(self):
        """Ticker should handle zero prices"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "0.00000000",
            "a": "0.00000000",
            "B": "0.00",
            "A": "0.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price == 0.0
        assert ticker.ask_price == 0.0

    def test_ticker_max_prices(self):
        """Ticker should handle very large prices"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "999999999999.99999999",
            "a": "999999999999.99999999",
            "B": "999999999999.99",
            "A": "999999999999.99",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price >= 1e12
        assert ticker.ask_price >= 1e12

    def test_ticker_negative_prices(self):
        """Ticker should handle negative prices (invalid but should not crash)"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "-100.00",
            "a": "-101.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price == -100.0
        assert ticker.ask_price == -101.0

    def test_ticker_missing_fields(self):
        """Ticker should handle missing optional fields"""
        data = {"s": "BTCUSDT", "E": 1234567890.123}
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == "BTCUSDT"
        assert ticker.server_time == 1234567890.123

    def test_ticker_empty_strings(self):
        """Ticker should handle empty string values"""
        data = {
            "s": "",
            "E": 1234567890.123,
            "b": "",
            "a": "",
            "B": "",
            "A": "",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()

    def test_ticker_invalid_numeric_strings(self):
        """Ticker should handle invalid numeric strings"""
        data = {
            "s": "BTCUSDT",
            "E": "invalid",
            "b": "not_a_number",
            "a": "NaN",
            "B": "Inf",
            "A": "-Inf",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()

    def test_ticker_extreme_precision(self):
        """Ticker should handle extreme decimal precision"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.12345678901234567890,
            "b": "50000.12345678901234567890",
            "a": "50001.12345678901234567890",
            "B": "0.000000000000000001",
            "A": "0.000000000000000002",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price > 50000
        assert ticker.ask_price > 50001

    def test_ticker_scientific_notation(self):
        """Ticker should handle scientific notation"""
        data = {
            "s": "BTCUSDT",
            "E": 1.234567890e9,
            "b": "5.0e4",
            "a": "5.001e4",
            "B": "1e-8",
            "A": "2e-8",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price == 50000.0
        assert ticker.ask_price == 50010.0

    def test_ticker_unicode_symbol(self):
        """Ticker should handle unicode in symbol names"""
        data = {
            "s": "测试USDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "测试USDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == "测试USDT"

    def test_ticker_special_characters_symbol(self):
        """Ticker should handle special characters in symbol"""
        data = {
            "s": "BTC-USDT@123",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTC-USDT@123", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == "BTC-USDT@123"

    def test_ticker_very_long_symbol(self):
        """Ticker should handle very long symbol names"""
        long_symbol = "A" * 1000
        data = {
            "s": long_symbol,
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), long_symbol, "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == long_symbol


class TestOrderEdgeCases:
    """Test edge cases for order data containers"""

    def test_order_zero_quantity(self):
        """Order should handle zero quantity"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "50000.00",
                "q": "0.00000000",
                "ap": "0.00",
                "X": "NEW",
                "T": 1234567890.123,
                "l": "0.00",
                "z": "0.00",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_qty == 0.0

    def test_order_max_quantity(self):
        """Order should handle very large quantities"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "50000.00",
                "q": "999999999999.99999999",
                "ap": "0.00",
                "X": "NEW",
                "T": 1234567890.123,
                "l": "0.00",
                "z": "0.00",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_qty >= 1e12

    def test_order_zero_price(self):
        """Order should handle zero price"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "0.00000000",
                "q": "1.00",
                "ap": "0.00",
                "X": "NEW",
                "T": 1234567890.123,
                "l": "0.00",
                "z": "0.00",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_price == 0.0

    def test_order_negative_price(self):
        """Order should handle negative prices"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "-50000.00",
                "q": "1.00",
                "ap": "0.00",
                "X": "NEW",
                "T": 1234567890.123,
                "l": "0.00",
                "z": "0.00",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_price == -50000.0

    def test_order_missing_optional_fields(self):
        """Order should handle missing optional fields"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "X": "NEW",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_symbol_name == "BTCUSDT"
        assert order.order_side == "BUY"

    def test_order_extreme_precision(self):
        """Order should handle extreme decimal precision"""
        data = {
            "E": 1234567890.12345678901234567890,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "50000.12345678901234567890",
                "q": "0.000000000000000001",
                "ap": "50000.12345678901234567890",
                "X": "FILLED",
                "T": 1234567890.12345678901234567890,
                "l": "0.000000000000000001",
                "z": "0.000000000000000001",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_price > 50000

    def test_order_unicode_symbol(self):
        """Order should handle unicode in symbol names"""
        data = {
            "E": 1234567890.123,
            "o": {
                "s": "测试USDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "50000.00",
                "q": "1.00",
                "ap": "0.00",
                "X": "NEW",
                "T": 1234567890.123,
                "l": "0.00",
                "z": "0.00",
            },
        }
        order = BinanceForceOrderData(
            json.dumps(data), "测试USDT", "SPOT", has_been_json_encoded=False
        )
        order.init_data()
        assert order.order_symbol_name == "测试USDT"


class TestBalanceEdgeCases:
    """Test edge cases for balance data containers"""

    def test_balance_zero_balance(self):
        """Balance should handle zero balance"""
        data = {
            "a": {
                "B": [
                    {"a": "BTC", "f": "0.00000000", "l": "0.00000000"},
                ]
            }
        }
        balance = BinanceWssBalanceData(json.dumps(data), "SPOT", has_been_json_encoded=False)
        balance.init_balance_data()

    def test_balance_max_balance(self):
        """Balance should handle very large balances"""
        data = {
            "a": {
                "B": [
                    {
                        "a": "BTC",
                        "f": "999999999999.99999999",
                        "l": "999999999999.99999999",
                    },
                ]
            }
        }
        balance = BinanceWssBalanceData(json.dumps(data), "SPOT", has_been_json_encoded=False)
        balance.init_balance_data()

    def test_balance_negative_balance(self):
        """Balance should handle negative values"""
        data = {
            "a": {
                "B": [
                    {"a": "BTC", "f": "-100.00", "l": "-50.00"},
                ]
            }
        }
        balance = BinanceWssBalanceData(json.dumps(data), "SPOT", has_been_json_encoded=False)
        balance.init_balance_data()

    def test_balance_missing_fields(self):
        """Balance should handle missing optional fields"""
        data = {"a": {"B": [{"a": "BTC"}]}}
        balance = BinanceWssBalanceData(json.dumps(data), "SPOT", has_been_json_encoded=False)
        balance.init_balance_data()

    def test_balance_empty_asset_list(self):
        """Balance should handle empty asset list"""
        data = {"a": {"B": []}}
        balance = BinanceWssBalanceData(json.dumps(data), "SPOT", has_been_json_encoded=False)
        balance.init_balance_data()


class TestJSONParsingEdgeCases:
    """Test edge cases for JSON parsing in containers"""

    def test_malformed_json(self):
        """Container should handle malformed JSON"""
        with pytest.raises(json.JSONDecodeError):
            ticker = BinanceWssTickerData(
                "{invalid json}", "BTCUSDT", "SPOT", has_been_json_encoded=False
            )
            ticker.init_data()

    def test_empty_json_object(self):
        """Container should handle empty JSON object"""
        data = {}
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()

    def test_nested_json(self):
        """Container should handle nested JSON structures"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
            "extra": {"nested": {"deeply": {"value": 123}}},
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == "BTCUSDT"

    def test_json_with_null_values(self):
        """Container should handle null values in JSON"""
        data = {
            "s": "BTCUSDT",
            "E": None,
            "b": None,
            "a": None,
            "B": None,
            "A": None,
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()

    def test_json_with_boolean_values(self):
        """Container should handle boolean values in JSON"""
        data = {
            "s": "BTCUSDT",
            "E": True,
            "b": False,
            "a": True,
            "B": False,
            "A": True,
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()


class TestRequestTickerEdgeCases:
    """Test edge cases for request ticker data"""

    def test_request_ticker_zero_prices(self):
        """Request ticker should handle zero prices"""
        data = {
            "symbol": "BTCUSDT",
            "time": 1234567890,
            "bidPrice": "0.00000000",
            "askPrice": "0.00000000",
            "bidQty": "0.00",
            "askQty": "0.00",
        }
        ticker = BinanceRequestTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.bid_price == 0.0
        assert ticker.ask_price == 0.0

    def test_request_ticker_missing_fields(self):
        """Request ticker should handle missing fields"""
        data = {"symbol": "BTCUSDT"}
        ticker = BinanceRequestTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        assert ticker.ticker_symbol_name == "BTCUSDT"

    def test_request_ticker_invalid_numeric_strings(self):
        """Request ticker should handle invalid numeric strings"""
        data = {
            "symbol": "BTCUSDT",
            "time": "invalid",
            "bidPrice": "not_a_number",
            "askPrice": "NaN",
            "bidQty": "Inf",
            "askQty": "-Inf",
        }
        ticker = BinanceRequestTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()


class TestDataContainerMethods:
    """Test edge cases for data container methods"""

    def test_get_all_data_multiple_calls(self):
        """get_all_data should be idempotent"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        all_data1 = ticker.get_all_data()
        all_data2 = ticker.get_all_data()
        assert all_data1 == all_data2

    def test_str_representation(self):
        """String representation should not raise errors"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        str_repr = str(ticker)
        assert isinstance(str_repr, str)
        assert len(str_repr) > 0

    def test_repr_representation(self):
        """Repr representation should not raise errors"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        repr_str = repr(ticker)
        assert isinstance(repr_str, str)
        assert len(repr_str) > 0


class TestConcurrentInitialization:
    """Test concurrent initialization edge cases"""

    def test_double_initialization(self):
        """Double initialization should be safe"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(
            json.dumps(data), "BTCUSDT", "SPOT", has_been_json_encoded=False
        )
        ticker.init_data()
        ticker.init_data()
        assert ticker.has_been_init_data is True

    def test_json_already_encoded(self):
        """Container should handle pre-encoded JSON"""
        data = {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
            "B": "10.00",
            "A": "10.00",
        }
        ticker = BinanceWssTickerData(data, "BTCUSDT", "SPOT", has_been_json_encoded=True)
        ticker.init_data()
        assert ticker.has_been_json_encoded is True
