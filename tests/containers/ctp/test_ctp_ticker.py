"""Tests for CTP ticker container."""


from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData


class TestCtpTickerData:
    """Tests for CtpTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CtpTickerData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert ticker.exchange_name == "CTP"
        assert ticker.symbol_name == "rb2505"
        assert ticker.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "InstrumentID": "rb2505",
            "LastPrice": 3500.0,
            "PreSettlementPrice": 3450.0,
            "PreClosePrice": 3460.0,
            "OpenPrice": 3480.0,
            "HighestPrice": 3550.0,
            "LowestPrice": 3470.0,
            "BidPrice1": 3499.0,
            "BidVolume1": 10,
            "AskPrice1": 3501.0,
            "AskVolume1": 15,
            "Volume": 10000,
            "Turnover": 35000000.0,
            "OpenInterest": 50000.0,
            "UpperLimitPrice": 3800.0,
            "LowerLimitPrice": 3200.0,
            "UpdateTime": "14:30:00",
            "UpdateMillisec": 500,
            "TradingDay": "20250404",
            "ExchangeID": "SHFE",
        }
        ticker = CtpTickerData(data, symbol_name="rb2505", asset_type="FUTURE")
        ticker.init_data()

        assert ticker.instrument_id == "rb2505"
        assert ticker.last_price_val == 3500.0
        assert ticker.pre_settlement_price == 3450.0
        assert ticker.open_price_val == 3480.0
        assert ticker.highest_price == 3550.0
        assert ticker.lowest_price == 3470.0
        assert ticker.bid_price_1 == 3499.0
        assert ticker.bid_volume_1 == 10
        assert ticker.ask_price_1 == 3501.0
        assert ticker.ask_volume_1 == 15
        assert ticker.volume_val == 10000
        assert ticker.turnover == 35000000.0
        assert ticker.open_interest == 50000.0
        assert ticker.upper_limit_price == 3800.0
        assert ticker.lower_limit_price == 3200.0
        assert ticker.update_time_val == "14:30:00"
        assert ticker.trading_day == "20250404"
        assert ticker.exchange_id == "SHFE"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "InstrumentID": "rb2505",
            "LastPrice": 3500.0,
        }
        ticker = CtpTickerData(data)
        ticker.init_data()
        first_price = ticker.last_price_val

        ticker.init_data()
        assert ticker.last_price_val == first_price

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        ticker = CtpTickerData({})
        assert ticker.get_exchange_name() == "CTP"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"InstrumentID": "rb2505"}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_symbol_name() == "rb2505"

    def test_get_ticker_symbol_name(self):
        """Test get_ticker_symbol_name."""
        data = {"InstrumentID": "rb2505"}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "rb2505"

    def test_get_bid_price(self):
        """Test get_bid_price."""
        data = {"BidPrice1": 3499.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_bid_price() == 3499.0

    def test_get_ask_price(self):
        """Test get_ask_price."""
        data = {"AskPrice1": 3501.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_ask_price() == 3501.0

    def test_get_bid_volume(self):
        """Test get_bid_volume."""
        data = {"BidVolume1": 10}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_bid_volume() == 10

    def test_get_ask_volume(self):
        """Test get_ask_volume."""
        data = {"AskVolume1": 15}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_ask_volume() == 15

    def test_get_last_price(self):
        """Test get_last_price."""
        data = {"LastPrice": 3500.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_last_price() == 3500.0

    def test_get_last_volume(self):
        """Test get_last_volume."""
        data = {"Volume": 10000}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_last_volume() == 10000

    def test_get_upper_limit_price(self):
        """Test get_upper_limit_price."""
        data = {"UpperLimitPrice": 3800.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_upper_limit_price() == 3800.0

    def test_get_lower_limit_price(self):
        """Test get_lower_limit_price."""
        data = {"LowerLimitPrice": 3200.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_lower_limit_price() == 3200.0

    def test_get_open_interest(self):
        """Test get_open_interest."""
        data = {"OpenInterest": 50000.0}
        ticker = CtpTickerData(data)
        ticker.init_data()

        assert ticker.get_open_interest() == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "InstrumentID": "rb2505",
            "LastPrice": 3500.0,
            "BidPrice1": 3499.0,
            "AskPrice1": 3501.0,
        }
        ticker = CtpTickerData(data)
        ticker.init_data()

        result = ticker.get_all_data()

        assert result["exchange_name"] == "CTP"
        assert result["instrument_id"] == "rb2505"
        assert result["last_price"] == 3500.0
        assert result["bid_price_1"] == 3499.0
        assert result["ask_price_1"] == 3501.0
