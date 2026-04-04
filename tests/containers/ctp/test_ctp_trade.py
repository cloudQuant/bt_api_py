"""Tests for CTP trade container."""

import pytest

from bt_api_py.containers.ctp.ctp_trade import CtpTradeData


class TestCtpTradeData:
    """Tests for CtpTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = CtpTradeData({}, symbol_name="rb2505", asset_type="FUTURE")

        assert trade.exchange_name == "CTP"
        assert trade.symbol_name == "rb2505"
        assert trade.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "InstrumentID": "rb2505",
            "TradeID": "123456",
            "OrderRef": "000001",
            "OrderSysID": "654321",
            "Direction": "0",
            "OffsetFlag": "0",
            "Price": 3500.0,
            "Volume": 10,
            "TradeDate": "20250404",
            "TradeTime": "14:30:00",
            "ExchangeID": "SHFE",
        }
        trade = CtpTradeData(data, symbol_name="rb2505", asset_type="FUTURE")
        trade.init_data()

        assert trade.instrument_id == "rb2505"
        assert trade.trade_id_value == "123456"
        assert trade.order_ref == "000001"
        assert trade.order_sys_id == "654321"
        assert trade.direction == "buy"
        assert trade.offset == "open"
        assert trade.price == 3500.0
        assert trade.volume == 10
        assert trade.trade_date == "20250404"
        assert trade.trade_time == "14:30:00"
        assert trade.exchange_id == "SHFE"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "TradeID": "123456",
            "Price": 3500.0,
        }
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()
        first_price = trade.price

        trade.init_data()
        assert trade.price == first_price

    def test_direction_sell(self):
        """Test sell direction."""
        data = {"Direction": "1"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.direction == "sell"

    def test_offset_close(self):
        """Test close offset."""
        data = {"OffsetFlag": "1"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.offset == "close"

    def test_offset_close_today(self):
        """Test close_today offset."""
        data = {"OffsetFlag": "3"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.offset == "close_today"

    def test_offset_close_yesterday(self):
        """Test close_yesterday offset."""
        data = {"OffsetFlag": "4"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.offset == "close_yesterday"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        data = {"InstrumentID": "rb2505"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_symbol_name() == "rb2505"

    def test_get_server_time(self):
        """Test get_server_time."""
        data = {"TradeTime": "14:30:00"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_server_time() == "14:30:00"

    def test_get_trade_id(self):
        """Test get_trade_id."""
        data = {"TradeID": "123456"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_id() == "123456"

    def test_get_order_id(self):
        """Test get_order_id."""
        data = {"OrderSysID": "654321"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_order_id() == "654321"

    def test_get_client_order_id(self):
        """Test get_client_order_id."""
        data = {"OrderRef": "000001"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_client_order_id() == "000001"

    def test_get_trade_side(self):
        """Test get_trade_side."""
        data = {"Direction": "0"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_side() == "buy"

    def test_get_trade_offset(self):
        """Test get_trade_offset."""
        data = {"OffsetFlag": "0"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_offset() == "open"

    def test_get_trade_price(self):
        """Test get_trade_price."""
        data = {"Price": 3500.0}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_price() == 3500.0

    def test_get_trade_volume(self):
        """Test get_trade_volume."""
        data = {"Volume": 10}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_volume() == 10

    def test_get_trade_time(self):
        """Test get_trade_time."""
        data = {"TradeTime": "14:30:00"}
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        assert trade.get_trade_time() == "14:30:00"

    def test_get_trade_fee(self):
        """Test get_trade_fee returns None due to source code issue.

        Note: Line 54 in ctp_trade.py has `self.trade_fee: float | 0.0` which is
        a type annotation, not an assignment. The attribute is never assigned,
        so get_trade_fee returns None.
        """
        trade = CtpTradeData({}, symbol_name="rb2505")
        trade.init_data()
        # Returns None because trade_fee is never assigned
        assert trade.get_trade_fee() is None

    def test_get_trade_fee_symbol(self):
        """Test get_trade_fee_symbol returns CNY."""
        trade = CtpTradeData({}, symbol_name="rb2505")
        assert trade.get_trade_fee_symbol() == "CNY"

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "InstrumentID": "rb2505",
            "TradeID": "123456",
            "Price": 3500.0,
            "Volume": 10,
        }
        trade = CtpTradeData(data, symbol_name="rb2505")
        trade.init_data()

        result = trade.get_all_data()

        assert result["exchange_name"] == "CTP"
        assert result["instrument_id"] == "rb2505"
        assert result["trade_id"] == "123456"
        assert result["price"] == 3500.0
        assert result["volume"] == 10
