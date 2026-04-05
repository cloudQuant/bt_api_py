"""Tests for CTP bar container."""


from bt_api_py.containers.ctp.ctp_bar import CtpBarData


class TestCtpBarData:
    """Tests for CtpBarData."""

    def test_init(self):
        """Test initialization."""
        bar = CtpBarData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert bar.exchange_name == "CTP"
        assert bar.symbol_name == "rb2505"
        assert bar.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with bar info."""
        data = {
            "open_time": "20250404 09:00:00",
            "close_time": "20250404 10:15:00",
            "open": 3500.0,
            "high": 3550.0,
            "low": 3480.0,
            "close": 3520.0,
            "volume": 10000,
            "amount": 35000000.0,
            "open_interest": 50000.0,
            "settlement_price": 3510.0,
        }
        bar = CtpBarData(data, symbol_name="rb2505", asset_type="FUTURE")
        bar.init_data()

        assert bar.open_time == "20250404 09:00:00"
        assert bar.close_time == "20250404 10:15:00"
        assert bar.open_price == 3500.0
        assert bar.high_price == 3550.0
        assert bar.low_price == 3480.0
        assert bar.close_price == 3520.0
        assert bar.volume_val == 10000
        assert bar.amount_val == 35000000.0
        assert bar.open_interest == 50000.0
        assert bar.settlement_price_val == 3510.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "open": 3500.0,
            "close": 3520.0,
        }
        bar = CtpBarData(data)
        bar.init_data()
        first_open = bar.open_price

        bar.init_data()
        assert bar.open_price == first_open

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        bar = CtpBarData({})
        assert bar.get_exchange_name() == "CTP"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        bar = CtpBarData({}, symbol_name="rb2505")
        assert bar.get_symbol_name() == "rb2505"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        bar = CtpBarData({}, asset_type="FUTURE")
        assert bar.get_asset_type() == "FUTURE"

    def test_get_server_time(self):
        """Test get_server_time returns close_time."""
        data = {"close_time": "20250404 10:15:00"}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_server_time() == "20250404 10:15:00"

    def test_get_open_time(self):
        """Test get_open_time."""
        data = {"open_time": "20250404 09:00:00"}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_open_time() == "20250404 09:00:00"

    def test_get_open_price(self):
        """Test get_open_price."""
        data = {"open": 3500.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_open_price() == 3500.0

    def test_get_high_price(self):
        """Test get_high_price."""
        data = {"high": 3550.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_high_price() == 3550.0

    def test_get_low_price(self):
        """Test get_low_price."""
        data = {"low": 3480.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_low_price() == 3480.0

    def test_get_close_price(self):
        """Test get_close_price."""
        data = {"close": 3520.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_close_price() == 3520.0

    def test_get_volume(self):
        """Test get_volume."""
        data = {"volume": 10000}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_volume() == 10000

    def test_get_amount(self):
        """Test get_amount."""
        data = {"amount": 35000000.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_amount() == 35000000.0

    def test_get_close_time(self):
        """Test get_close_time."""
        data = {"close_time": "20250404 10:15:00"}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_close_time() == "20250404 10:15:00"

    def test_get_bar_status(self):
        """Test get_bar_status always returns True."""
        bar = CtpBarData({})
        assert bar.get_bar_status() is True

    def test_get_open_interest(self):
        """Test get_open_interest (CTP specific)."""
        data = {"open_interest": 50000.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_open_interest() == 50000.0

    def test_get_settlement_price(self):
        """Test get_settlement_price (CTP specific)."""
        data = {"settlement_price": 3510.0}
        bar = CtpBarData(data)
        bar.init_data()

        assert bar.get_settlement_price() == 3510.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "open": 3500.0,
            "high": 3550.0,
            "low": 3480.0,
            "close": 3520.0,
            "volume": 10000,
        }
        bar = CtpBarData(data, symbol_name="rb2505")
        bar.init_data()

        result = bar.get_all_data()

        assert result["exchange_name"] == "CTP"
        assert result["symbol_name"] == "rb2505"
        assert result["open"] == 3500.0
        assert result["close"] == 3520.0
        assert result["volume"] == 10000
