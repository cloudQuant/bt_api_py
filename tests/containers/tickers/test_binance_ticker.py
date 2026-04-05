from __future__ import annotations

import json

import pytest

from bt_api_py.containers.tickers.binance_ticker import (
    BinanceRequestTickerData,
    BinanceTickerData,
    BinanceWssTickerData,
)


class TestBinanceTickerData:
    """Tests for BinanceTickerData base class - tests only non-abstract methods."""

    def test_init(self):
        """Test initialization."""
        ticker = BinanceTickerData(
            ticker_info={},
            symbol_name="BTC-USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        assert ticker.exchange_name == "BINANCE"
        assert ticker.symbol_name == "BTC-USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.event == "TickerEvent"

    def test_get_event(self):
        """Test get_event method."""
        ticker = BinanceTickerData({}, "BTC-USDT", "SPOT", True)
        assert ticker.get_event() == "TickerEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError in base class."""
        ticker = BinanceTickerData({}, "BTC-USDT", "SPOT", True)

        with pytest.raises(NotImplementedError):
            ticker.init_data()


class TestBinanceWssTickerData:
    """Tests for BinanceWssTickerData."""

    def test_init_data_full(self):
        """Test init_data with full ticker data."""
        data = {
            "e": "24hrTicker",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "b": "50000.00",
            "B": "10.0",
            "a": "50001.00",
            "A": "5.0",
            "c": "50000.50",
            "Q": "1.0",
            "o": "49000.00",
            "h": "51000.00",
            "l": "48000.00",
            "x": "49500.00",
            "v": "10000.0",
            "q": "500000000.0",
            "p": "500.50",
            "P": "1.01",
        }
        ticker = BinanceWssTickerData(data, "BTC-USDT", "SPOT", True)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "BTCUSDT"
        assert ticker.get_server_time() == 1700000000000.0
        assert ticker.get_bid_price() == 50000.0
        assert ticker.get_bid_volume() == 10.0
        assert ticker.get_ask_price() == 50001.0
        assert ticker.get_ask_volume() == 5.0
        assert ticker.get_last_price() == 50000.50
        assert ticker.get_last_volume() == 1.0
        assert ticker.get_open_price() == 49000.0
        assert ticker.get_high_price() == 51000.0
        assert ticker.get_low_price() == 48000.0
        assert ticker.get_prev_close() == 49500.0
        assert ticker.get_volume_24h() == 10000.0
        assert ticker.get_turnover_24h() == 500000000.0
        assert ticker.get_price_change() == 500.50
        assert ticker.get_price_change_pct() == 1.01

    def test_init_data_with_json_string(self):
        """Test init_data with JSON string."""
        data = '{"s": "ETHUSDT", "b": "3000.00", "a": "3001.00"}'
        ticker = BinanceWssTickerData(data, "ETH-USDT", "SPOT", False)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "ETHUSDT"
        assert ticker.get_bid_price() == 3000.0
        assert ticker.get_ask_price() == 3001.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"s": "BTCUSDT", "b": "50000.00"}
        ticker = BinanceWssTickerData(data, "BTC-USDT", "SPOT", True)

        ticker.init_data()
        first_bid = ticker.get_bid_price()

        ticker.init_data()
        assert ticker.get_bid_price() == first_bid

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"s": "BTCUSDT", "b": "50000.00"}
        ticker = BinanceWssTickerData(data, "BTC-USDT", "SPOT", True)

        result = str(ticker)
        parsed = json.loads(result)

        assert parsed["ticker_symbol_name"] == "BTCUSDT"
        assert parsed["bid_price"] == 50000.0

    def test_repr_equals_str(self):
        """Test __repr__ equals __str__."""
        data = {"s": "BTCUSDT"}
        ticker = BinanceWssTickerData(data, "BTC-USDT", "SPOT", True)

        assert repr(ticker) == str(ticker)


class TestBinanceRequestTickerData:
    """Tests for BinanceRequestTickerData."""

    def test_init_data_full(self):
        """Test init_data with full ticker data."""
        data = {
            "symbol": "BTCUSDT",
            "bidPrice": "50000.00",
            "bidQty": "10.0",
            "askPrice": "50001.00",
            "askQty": "5.0",
            "time": 1700000000000,
        }
        ticker = BinanceRequestTickerData(data, "BTC-USDT", "SPOT", True)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "BTCUSDT"
        assert ticker.get_server_time() == 1700000000000.0
        assert ticker.get_bid_price() == 50000.0
        assert ticker.get_bid_volume() == 10.0
        assert ticker.get_ask_price() == 50001.0
        assert ticker.get_ask_volume() == 5.0

    def test_init_data_with_json_string(self):
        """Test init_data with JSON string."""
        data = '{"symbol": "ETHUSDT", "bidPrice": "3000.00", "askPrice": "3001.00"}'
        ticker = BinanceRequestTickerData(data, "ETH-USDT", "SPOT", False)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "ETHUSDT"
        assert ticker.get_bid_price() == 3000.0
        assert ticker.get_ask_price() == 3001.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"symbol": "BTCUSDT", "bidPrice": "50000.00"}
        ticker = BinanceRequestTickerData(data, "BTC-USDT", "SPOT", True)

        ticker.init_data()
        first_bid = ticker.get_bid_price()

        ticker.init_data()
        assert ticker.get_bid_price() == first_bid


@pytest.mark.ticker
def test_binance_request_ticker():
    data = {
        "lastUpdateId": 1027024,
        "symbol": "BTCUSDT",  # 交易对
        "bidPrice": "4.00000000",  # 最优买单价
        "bidQty": "431.00000000",  # 挂单量
        "askPrice": "4.00000200",  # 最优卖单价
        "askQty": "9.00000000",  # 挂单量
        "time": 1589437530011,  # 撮合引擎时间
    }
    bt = BinanceRequestTickerData(data, "BTC-USDT", "PERPETUAL", True)
    bt.init_data()
    assert bt.get_server_time() == 1589437530011.0
    assert bt.get_exchange_name() == "BINANCE"
    assert bt.get_symbol_name() == "BTC-USDT"
    assert bt.get_ticker_symbol_name() == "BTCUSDT"
    assert bt.get_bid_price() == 4.00000000
    assert bt.get_bid_volume() == 431.00000000
    assert bt.get_ask_price() == 4.00000200
    assert bt.get_ask_volume() == 9.00000000
    assert bt.get_last_price() is None
    assert bt.get_last_volume() is None


@pytest.mark.ticker
def test_binance_ticker():
    data = {
        "e": "bookTicker",  # 事件类型
        "u": 400900217,  # 更新ID
        "E": 1568014460893,  # 事件推送时间
        "T": 1568014460891,  # 撮合时间
        "s": "BNBUSDT",  # 交易对
        "b": "25.35190000",  # 买单最优挂单价格
        "B": "31.21000000",  # 买单最优挂单数量
        "a": "25.36520000",  # 卖单最优挂单价格
        "A": "40.66000000",  # 卖单最优挂单数量
    }
    bt = BinanceWssTickerData(data, "BNB-USDT", "PERPETUAL", True)
    bt.init_data()
    assert bt.get_server_time() == 1568014460893.0
    assert bt.get_exchange_name() == "BINANCE"
    assert bt.get_symbol_name() == "BNB-USDT"
    assert bt.get_ticker_symbol_name() == "BNBUSDT"
    assert bt.get_bid_price() == 25.35190000
    assert bt.get_bid_volume() == 31.21000000
    assert bt.get_ask_price() == 25.36520000
    assert bt.get_ask_volume() == 40.66000000
    assert bt.get_last_price() is None
    assert bt.get_last_volume() is None


if __name__ == "__main__":
    test_binance_ticker()
