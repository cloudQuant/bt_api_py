"""
Test data fixtures for bt_api_py tests
Centralized test data management
"""

import pytest

# ============== BINANCE FIXTURES ==============


@pytest.fixture
def binance_spot_ticker_data():
    """Standard Binance spot ticker test data"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.123,
        "b": "50000.00",
        "a": "50001.00",
        "B": "10.00",
        "A": "10.00",
    }


@pytest.fixture
def binance_request_ticker_data():
    """Standard Binance request ticker test data"""
    return {
        "symbol": "BTCUSDT",
        "bidPrice": "50000.00",
        "askPrice": "50001.00",
        "bidQty": "10.00",
        "askQty": "10.00",
    }


@pytest.fixture
def binance_order_data():
    """Standard Binance order test data"""
    return {
        "E": 1234567890.123,
        "o": {
            "s": "BTCUSDT",
            "S": "BUY",
            "o": "LIMIT",
            "f": "GTC",
            "p": "50000.00",
            "q": "0.1",
            "ap": "0.0",
            "X": "NEW",
            "T": 1234567890.123,
            "l": "0.0",
            "z": "0.0",
        },
    }


@pytest.fixture
def binance_balance_data():
    """Standard Binance balance test data"""
    return {"a": {"B": [{"a": "BTC", "f": "1.5", "l": "0.5"}]}}


# ============== OKX FIXTURES ==============
@pytest.fixture
def okx_ticker_data():
    """Standard OKX ticker test data"""
    return {
        "instId": "BTC-USDT",
        "last": "50000.50",
        "bidPx": "50000.00",
        "askPx": "50001.00",
        "bidSz": "10.00",
        "askSz": "10.00",
    }


@pytest.fixture
def okx_order_data():
    """Standard OKX order test data"""
    return {
        "instId": "BTC-USDT",
        "ordId": "123456",
        "tag": "12345",
        "side": "buy",
        "sz": "0.1",
        "px": "50000",
    }


# ============== EDGE CASE FIXTURES ==============
@pytest.fixture
def zero_price_data():
    """Data with zero prices"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.123,
        "b": "0.00000000",
        "a": "0.00000000",
        "B": "0.00",
        "A": "0.00",
    }


@pytest.fixture
def max_price_data():
    """Data with maximum prices"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.123,
        "b": "999999999999.99999999",
        "a": "999999999999.99999999",
        "B": "999999999999.99",
        "A": "999999999999.99",
    }


@pytest.fixture
def negative_price_data():
    """Data with negative prices (invalid but should not crash)"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.123,
        "b": "-100.00",
        "a": "-101.00",
        "B": "10.00",
        "A": "10.00",
    }


@pytest.fixture
def missing_fields_data():
    """Data with missing optional fields"""
    return {"s": "BTCUSDT", "E": 1234567890.123}


@pytest.fixture
def empty_strings_data():
    """Data with empty string values"""
    return {
        "s": "",
        "E": 1234567890.123,
        "b": "",
        "a": "",
        "B": "",
        "A": "",
    }


@pytest.fixture
def unicode_symbol_data():
    """Data with unicode in symbol names"""
    return {
        "s": "测试USDT",
        "E": 1234567890.123,
        "b": "50000.00",
        "a": "50001.00",
        "B": "10.00",
        "A": "10.00",
    }


# ============== MULTI-EXCHANGE FIXTURES ==============
@pytest.fixture
def multi_exchange_tickers():
    """Test data for multiple exchanges"""
    return {
        "binance": {
            "s": "BTCUSDT",
            "E": 1234567890.123,
            "b": "50000.00",
            "a": "50001.00",
        },
        "okx": {
            "instId": "BTC-USDT",
            "last": "50000.50",
            "bidPx": "50000.00",
            "askPx": "50001.00",
        },
    }


# ============== INVALID DATA FIXTURES ==============
@pytest.fixture
def invalid_numeric_data():
    """Data with invalid numeric values"""
    return {
        "s": "BTCUSDT",
        "E": "invalid",
        "b": "not_a_number",
        "a": "NaN",
        "B": "Inf",
        "A": "-Inf",
    }


@pytest.fixture
def malformed_json_data():
    """Invalid JSON data"""
    return "{invalid json"


@pytest.fixture
def empty_json_data():
    """Empty JSON object"""
    return {}


@pytest.fixture
def nested_json_data():
    """Deeply nested JSON structure"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.123,
        "b": "50000.00",
        "a": "50001.00",
        "B": "10.00",
        "A": "10.00",
        "extra": {"nested": {"deeply": {"value": 123}}},
    }


# ============== SPECIAL VALUE FIXTURES ==============
@pytest.fixture
def extreme_precision_data():
    """Data with extreme decimal precision"""
    return {
        "s": "BTCUSDT",
        "E": 1234567890.12345678901234567890,
        "b": "50000.12345678901234567890",
        "a": "50001.12345678901234567890",
        "B": "0.000000000000000001",
        "A": "0.000000000000000002",
    }


@pytest.fixture
def scientific_notation_data():
    """Data with scientific notation"""
    return {
        "s": "BTCUSDT",
        "E": 1.234567890e9,
        "b": "5.0e4",
        "a": "5.001e4",
        "B": "1e-8",
        "A": "2e-8",
    }
