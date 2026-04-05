"""Tests for IB ticker container."""

from bt_api_py.containers.ib.ib_ticker import IbTickerData


class TestIbTickerData:
    """Tests for IbTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = IbTickerData({}, symbol_name="AAPL", asset_type="STK")

        assert ticker.exchange_name == "IB"
        assert ticker.symbol_name == "AAPL"
        assert ticker.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "symbol": "AAPL",
            "bid": 150.0,
            "ask": 150.5,
            "bidSize": 100,
            "askSize": 200,
            "last": 150.25,
            "lastSize": 50,
            "volume": 1000000,
            "high": 152.0,
            "low": 149.0,
            "close": 151.0,
            "time": 1712217600,
        }
        ticker = IbTickerData(data, symbol_name="AAPL", asset_type="STK")
        ticker.init_data()

        assert ticker.contract_symbol == "AAPL"
        assert ticker.bid_val == 150.0
        assert ticker.ask_val == 150.5
        assert ticker.bid_size_val == 100
        assert ticker.ask_size_val == 200
        assert ticker.last_val == 150.25
        assert ticker.last_size_val == 50
        assert ticker.volume_val == 1000000
        assert ticker.high_val == 152.0
        assert ticker.low_val == 149.0
        assert ticker.close_val == 151.0
        assert ticker.timestamp_val == 1712217600

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "symbol": "AAPL",
            "bid": 150.0,
        }
        ticker = IbTickerData(data)
        ticker.init_data()
        first_bid = ticker.bid_val

        ticker.init_data()
        assert ticker.bid_val == first_bid

    def test_init_data_none_values(self):
        """Test init_data handles None values."""
        data = {
            "bid": None,
            "ask": None,
            "last": None,
        }
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.bid_val is None
        assert ticker.ask_val is None
        assert ticker.last_val is None

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        ticker = IbTickerData({})
        assert ticker.get_exchange_name() == "IB"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"symbol": "AAPL"}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_symbol_name() == "AAPL"

    def test_get_symbol_name_fallback(self):
        """Test get_symbol_name fallback to symbol_name."""
        ticker = IbTickerData({}, symbol_name="MSFT")
        ticker.init_data()

        assert ticker.get_symbol_name() == "MSFT"

    def test_get_ticker_symbol_name(self):
        """Test get_ticker_symbol_name."""
        data = {"symbol": "AAPL"}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "AAPL"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        ticker = IbTickerData({}, asset_type="STK")
        assert ticker.get_asset_type() == "STK"

    def test_get_server_time_numeric(self):
        """Test get_server_time with numeric timestamp."""
        data = {"time": 1712217600}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_server_time() == 1712217600.0

    def test_get_server_time_string(self):
        """Test get_server_time with string timestamp returns None."""
        data = {"time": "14:30:00"}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_server_time() is None

    def test_get_local_update_time_numeric(self):
        """Test get_local_update_time with numeric timestamp."""
        data = {"time": 1712217600}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_local_update_time() == 1712217600.0

    def test_get_local_update_time_string(self):
        """Test get_local_update_time with string timestamp returns 0.0."""
        data = {"time": "14:30:00"}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_local_update_time() == 0.0

    def test_get_bid_price(self):
        """Test get_bid_price."""
        data = {"bid": 150.0}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_bid_price() == 150.0

    def test_get_ask_price(self):
        """Test get_ask_price."""
        data = {"ask": 150.5}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_ask_price() == 150.5

    def test_get_bid_volume(self):
        """Test get_bid_volume."""
        data = {"bidSize": 100}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_bid_volume() == 100

    def test_get_ask_volume(self):
        """Test get_ask_volume."""
        data = {"askSize": 200}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_ask_volume() == 200

    def test_get_last_price(self):
        """Test get_last_price."""
        data = {"last": 150.25}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_last_price() == 150.25

    def test_get_last_volume(self):
        """Test get_last_volume."""
        data = {"lastSize": 50}
        ticker = IbTickerData(data)
        ticker.init_data()

        assert ticker.get_last_volume() == 50

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "symbol": "AAPL",
            "bid": 150.0,
            "ask": 150.5,
            "volume": 1000000,
        }
        ticker = IbTickerData(data)
        ticker.init_data()

        result = ticker.get_all_data()

        assert result["exchange_name"] == "IB"
        assert result["symbol"] == "AAPL"
        assert result["bid"] == 150.0
        assert result["ask"] == 150.5
        assert result["volume"] == 1000000
