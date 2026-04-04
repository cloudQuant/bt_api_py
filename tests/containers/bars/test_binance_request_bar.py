import json
import pytest

from bt_api_py.containers.bars.binance_bar import BinanceRequestBarData, BinanceWssBarData


class TestBinanceRequestBarData:
    """Tests for BinanceRequestBarData."""

    def test_init(self):
        """Test initialization."""
        data = [1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"]
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", True)

        assert bar.exchange_name == "BINANCE"
        assert bar.symbol_name == "BTC-USDT"
        assert bar.asset_type == "SPOT"
        assert bar.event == "BarEvent"

    def test_init_data_with_json_string(self):
        """Test init_data with JSON string."""
        data = json.dumps([1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"])
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", False)
        bar.init_data()

        assert bar.open_price == 50000.0
        assert bar.close_price == 50500.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = [1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"]
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", True)

        bar.init_data()
        first_open = bar.open_price

        bar.init_data()
        assert bar.open_price == first_open

    def test_get_all_data(self):
        """Test get_all_data method."""
        data = [1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"]
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", True)

        result = bar.get_all_data()

        assert result["symbol_name"] == "BTC-USDT"
        assert result["open_price"] == 50000.0
        assert result["close_price"] == 50500.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = [1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"]
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", True)

        result = str(bar)
        parsed = json.loads(result)

        assert parsed["open_price"] == 50000.0

    def test_all_getter_methods(self):
        """Test all getter methods."""
        data = [1607444700000, "50000", "51000", "49000", "50500", "100", 1607444759999, "5000000", 500, "50", "2500000", "0"]
        bar = BinanceRequestBarData(data, "BTC-USDT", "SPOT", True)
        bar.init_data()

        assert bar.get_event_type() == "BarEvent"
        assert bar.get_exchange_name() == "BINANCE"
        assert bar.get_symbol_name() == "BTC-USDT"
        assert bar.get_asset_type() == "SPOT"
        assert bar.get_server_time() == 1607444759999.0
        assert bar.get_local_update_time() > 0
        assert bar.get_open_time() == 1607444700000.0
        assert bar.get_open_price() == 50000.0
        assert bar.get_high_price() == 51000.0
        assert bar.get_low_price() == 49000.0
        assert bar.get_close_price() == 50500.0
        assert bar.get_volume() == 100.0
        assert bar.get_amount() == 5000000.0
        assert bar.get_close_time() == 1607444759999.0
        assert bar.get_quote_asset_volume() is None
        assert bar.get_base_asset_volume() is None
        assert bar.get_num_trades() == 500.0
        assert bar.get_taker_buy_base_asset_volume() == 50.0
        assert bar.get_taker_buy_quote_asset_volume() == 2500000.0
        assert bar.get_bar_status() is True


class TestBinanceWssBarData:
    """Tests for BinanceWssBarData."""

    def test_init(self):
        """Test initialization."""
        data = {
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": False,
            },
        }
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", True)

        assert bar.exchange_name == "BINANCE"
        assert bar.symbol_name == "BTC-USDT"
        assert bar.asset_type == "SPOT"

    def test_init_data_with_json_string(self):
        """Test init_data with JSON string."""
        data = json.dumps({
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": False,
            },
        })
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", False)
        bar.init_data()

        assert bar.open_price == 50000.0
        assert bar.close_price == 50500.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": False,
            },
        }
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", True)

        bar.init_data()
        first_open = bar.open_price

        bar.init_data()
        assert bar.open_price == first_open

    def test_get_all_data(self):
        """Test get_all_data method."""
        data = {
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": True,
            },
        }
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", True)
        bar.init_data()

        result = bar.get_all_data()

        assert result["symbol_name"] == "BTC-USDT"
        assert result["open_price"] == 50000.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": True,
            },
        }
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", True)

        result = str(bar)
        parsed = json.loads(result)

        assert parsed["open_price"] == 50000.0

    def test_all_getter_methods(self):
        """Test all getter methods."""
        data = {
            "e": "kline",
            "E": 1607443058651,
            "k": {
                "t": 1607443020000,
                "T": 1607443079999,
                "o": "50000",
                "c": "50500",
                "h": "51000",
                "l": "49000",
                "v": "100",
                "q": "5000000",
                "n": 500,
                "V": "50",
                "Q": "2500000",
                "x": True,
            },
        }
        bar = BinanceWssBarData(data, "BTC-USDT", "SPOT", True)
        bar.init_data()

        assert bar.get_event_type() == "BarEvent"
        assert bar.get_exchange_name() == "BINANCE"
        assert bar.get_symbol_name() == "BTC-USDT"
        assert bar.get_asset_type() == "SPOT"
        assert bar.get_server_time() == 1607443058651.0
        assert bar.get_local_update_time() > 0
        assert bar.get_open_time() == 1607443020000.0
        assert bar.get_open_price() == 50000.0
        assert bar.get_high_price() == 51000.0
        assert bar.get_low_price() == 49000.0
        assert bar.get_close_price() == 50500.0
        assert bar.get_volume() == 100.0
        assert bar.get_amount() == 5000000.0
        assert bar.get_close_time() == 1607443079999.0
        assert bar.get_quote_asset_volume() is None
        assert bar.get_base_asset_volume() is None
        assert bar.get_num_trades() == 500.0
        assert bar.get_taker_buy_base_asset_volume() == 50.0
        assert bar.get_taker_buy_quote_asset_volume() == 2500000.0
        assert bar.get_bar_status() is True


@pytest.mark.kline
def test_binance_wss_bar_functions():
    # {"code":"0","msg":"","data":[["1696089660000","26990.4","27004.5","26990.3","27004.5","4794","47.94","1294336.087","1"]]}
    data = {
        "e": "continuous_kline",
        "E": 1607443058651,
        "ps": "BTCUSDT",
        "ct": "PERPETUAL",
        "k": {
            "t": 1607443020000,
            "T": 1607443079999,
            "i": "1m",
            "f": 116467658886,
            "L": 116468012423,
            "o": "18787.00",
            "c": "18804.04",
            "h": "18804.04",
            "l": "18786.54",
            "v": "197.664",
            "n": 543,
            "x": "false",
            "q": "3715253.19494",
            "V": "184.769",
            "Q": "3472925.84746",
            "B": "0",
        },
    }
    binance_bar_data = BinanceWssBarData(data, data["ps"], data["ct"], True)
    binance_bar_data.init_data()
    assert binance_bar_data.get_bar_status() == 0
    assert binance_bar_data.get_amount() == 3715253.19494
    assert binance_bar_data.get_volume() == 197.664
    assert binance_bar_data.get_low_price() == 18786.54
    assert binance_bar_data.get_close_price() == 18804.04
    assert binance_bar_data.get_open_price() == 18787.00
    assert binance_bar_data.get_high_price() == 18804.04
    assert binance_bar_data.get_open_time() == 1607443020000.0
    assert binance_bar_data.get_close_time() == 1607443079999.0
    assert binance_bar_data.get_asset_type() == "PERPETUAL"
    assert binance_bar_data.get_symbol_name() == "BTCUSDT"
    assert binance_bar_data.get_exchange_name() == "BINANCE"
    assert binance_bar_data.get_taker_buy_base_asset_volume() == 184.769


@pytest.mark.kline
def test_binance_req_bar_functions():
    # {"code":"0","msg":"","data":[["1696089660000","26990.4","27004.5","26990.3","27004.5","4794","47.94","1294336.087","1"]]}
    data = [
        1607444700000,
        "18879.99",
        "18900.00",
        "18878.98",
        "18896.13",
        "492.363",
        1607444759999,
        "9302145.66080",
        1874,
        "385.983",
        "7292402.33267",
        "0",
    ]
    symbol = "BTCUSDT"
    asset_type = "PERPETUAL"
    binance_bar_data = BinanceRequestBarData(data, symbol, asset_type, True)
    binance_bar_data.init_data()
    assert binance_bar_data.get_bar_status() is True
    assert binance_bar_data.get_amount() == 9302145.66080
    assert binance_bar_data.get_volume() == 492.363
    assert binance_bar_data.get_low_price() == 18878.98
    assert binance_bar_data.get_close_price() == 18896.13
    assert binance_bar_data.get_open_price() == 18879.99
    assert binance_bar_data.get_high_price() == 18900.00
    assert binance_bar_data.get_open_time() == 1607444700000.0
    assert binance_bar_data.get_close_time() == 1607444759999.0
    assert binance_bar_data.get_asset_type() == "PERPETUAL"
    assert binance_bar_data.get_symbol_name() == "BTCUSDT"
    assert binance_bar_data.get_exchange_name() == "BINANCE"
    assert binance_bar_data.get_taker_buy_base_asset_volume() == 385.983
    assert binance_bar_data.get_event_type() == "BarEvent"


if __name__ == "__main__":
    test_binance_wss_bar_functions()
    test_binance_req_bar_functions()
