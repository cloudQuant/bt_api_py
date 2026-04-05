"""Tests for IB bar container."""


from bt_api_py.containers.ib.ib_bar import IbBarData


class TestIbBarData:
    """Tests for IbBarData."""

    def test_init(self):
        """Test initialization."""
        bar = IbBarData({}, symbol_name="AAPL", asset_type="STK")

        assert bar.exchange_name == "IB"
        assert bar.symbol_name == "AAPL"
        assert bar.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with bar info."""
        data = {
            "date": "20250404 09:30:00",
            "open": 150.0,
            "high": 152.0,
            "low": 149.0,
            "close": 151.0,
            "volume": 1000000,
            "wap": 150.5,
            "barCount": 5000,
        }
        bar = IbBarData(data, symbol_name="AAPL", asset_type="STK")
        bar.init_data()

        assert bar.date_val == "20250404 09:30:00"
        assert bar.open_val == 150.0
        assert bar.high_val == 152.0
        assert bar.low_val == 149.0
        assert bar.close_val == 151.0
        assert bar.volume_val == 1000000
        assert bar.wap_val == 150.5
        assert bar.bar_count == 5000

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "open": 150.0,
            "close": 151.0,
        }
        bar = IbBarData(data)
        bar.init_data()
        first_open = bar.open_val

        bar.init_data()
        assert bar.open_val == first_open

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        bar = IbBarData({})
        assert bar.get_exchange_name() == "IB"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        bar = IbBarData({}, symbol_name="AAPL")
        assert bar.get_symbol_name() == "AAPL"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        bar = IbBarData({}, asset_type="STK")
        assert bar.get_asset_type() == "STK"

    def test_get_server_time(self):
        """Test get_server_time."""
        data = {"date": "20250404 09:30:00"}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_server_time() == "20250404 09:30:00"

    def test_get_open_time(self):
        """Test get_open_time."""
        data = {"date": "20250404 09:30:00"}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_open_time() == "20250404 09:30:00"

    def test_get_open_price(self):
        """Test get_open_price."""
        data = {"open": 150.0}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_open_price() == 150.0

    def test_get_high_price(self):
        """Test get_high_price."""
        data = {"high": 152.0}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_high_price() == 152.0

    def test_get_low_price(self):
        """Test get_low_price."""
        data = {"low": 149.0}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_low_price() == 149.0

    def test_get_close_price(self):
        """Test get_close_price."""
        data = {"close": 151.0}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_close_price() == 151.0

    def test_get_volume(self):
        """Test get_volume."""
        data = {"volume": 1000000}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_volume() == 1000000

    def test_get_amount(self):
        """Test get_amount returns None for IB."""
        bar = IbBarData({})
        assert bar.get_amount() is None

    def test_get_close_time(self):
        """Test get_close_time."""
        data = {"date": "20250404 09:30:00"}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_close_time() == "20250404 09:30:00"

    def test_get_bar_status(self):
        """Test get_bar_status always returns True."""
        bar = IbBarData({})
        assert bar.get_bar_status() is True

    def test_get_num_trades(self):
        """Test get_num_trades."""
        data = {"barCount": 5000}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_num_trades() == 5000

    def test_get_wap(self):
        """Test get_wap (weighted average price)."""
        data = {"wap": 150.5}
        bar = IbBarData(data)
        bar.init_data()

        assert bar.get_wap() == 150.5

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "date": "20250404 09:30:00",
            "open": 150.0,
            "close": 151.0,
            "volume": 1000000,
        }
        bar = IbBarData(data, symbol_name="AAPL")
        bar.init_data()

        result = bar.get_all_data()

        assert result["exchange_name"] == "IB"
        assert result["symbol_name"] == "AAPL"
        assert result["open"] == 150.0
        assert result["close"] == 151.0
        assert result["volume"] == 1000000
