"""Tests for IB trade container."""

import pytest

from bt_api_py.containers.ib.ib_trade import IbTradeData


class TestIbTradeData:
    """Tests for IbTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = IbTradeData({}, symbol_name="AAPL", asset_type="STK")

        assert trade.exchange_name == "IB"
        assert trade.symbol_name == "AAPL"
        assert trade.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "execId": "000123",
            "orderId": 456,
            "permId": 789,
            "side": "BOT",
            "shares": 100,
            "price": 150.0,
            "cumQty": 100,
            "avgPrice": 150.0,
            "time": "20250404 14:30:00",
            "commission": 1.0,
            "exchange": "SMART",
        }
        trade = IbTradeData(data, symbol_name="AAPL", asset_type="STK")
        trade.init_data()

        assert trade.exec_id == "000123"
        assert trade.order_id_val == 456
        assert trade.perm_id == 789
        assert trade.side == "BOT"
        assert trade.shares == 100
        assert trade.price_val == 150.0
        assert trade.cum_qty == 100
        assert trade.avg_price_val == 150.0
        assert trade.exec_time == "20250404 14:30:00"
        assert trade.commission_val == 1.0
        assert trade.exchange_val == "SMART"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "execId": "000123",
            "price": 150.0,
        }
        trade = IbTradeData(data)
        trade.init_data()
        first_price = trade.price_val

        trade.init_data()
        assert trade.price_val == first_price

    def test_side_sld(self):
        """Test SLD (sold) side."""
        data = {"side": "SLD"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.side == "SLD"

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        trade = IbTradeData({})
        assert trade.get_exchange_name() == "IB"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        trade = IbTradeData({}, asset_type="STK")
        assert trade.get_asset_type() == "STK"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        trade = IbTradeData({}, symbol_name="AAPL")
        assert trade.get_symbol_name() == "AAPL"

    def test_get_server_time(self):
        """Test get_server_time."""
        data = {"time": "20250404 14:30:00"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_server_time() == "20250404 14:30:00"

    def test_get_trade_id(self):
        """Test get_trade_id."""
        data = {"execId": "000123"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_id() == "000123"

    def test_get_order_id(self):
        """Test get_order_id."""
        data = {"orderId": 456}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_order_id() == 456

    def test_get_client_order_id(self):
        """Test get_client_order_id."""
        data = {"permId": 789}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_client_order_id() == 789

    def test_get_trade_side_buy(self):
        """Test get_trade_side for BOT (buy)."""
        data = {"side": "BOT"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_side() == "buy"

    def test_get_trade_side_sell(self):
        """Test get_trade_side for SLD (sell)."""
        data = {"side": "SLD"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_side() == "sell"

    def test_get_trade_offset(self):
        """Test get_trade_offset returns None for IB."""
        trade = IbTradeData({})
        assert trade.get_trade_offset() is None

    def test_get_trade_price(self):
        """Test get_trade_price."""
        data = {"price": 150.0}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_price() == 150.0

    def test_get_trade_volume(self):
        """Test get_trade_volume."""
        data = {"shares": 100}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_volume() == 100

    def test_get_trade_time(self):
        """Test get_trade_time."""
        data = {"time": "20250404 14:30:00"}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_time() == "20250404 14:30:00"

    def test_get_trade_fee(self):
        """Test get_trade_fee."""
        data = {"commission": 1.0}
        trade = IbTradeData(data)
        trade.init_data()

        assert trade.get_trade_fee() == 1.0

    def test_get_trade_fee_none(self):
        """Test get_trade_fee with None commission."""
        trade = IbTradeData({})
        trade.init_data()

        assert trade.get_trade_fee() == 0.0

    def test_get_trade_fee_symbol(self):
        """Test get_trade_fee_symbol returns USD."""
        trade = IbTradeData({})
        assert trade.get_trade_fee_symbol() == "USD"

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "execId": "000123",
            "orderId": 456,
            "side": "BOT",
            "shares": 100,
            "price": 150.0,
        }
        trade = IbTradeData(data)
        trade.init_data()

        result = trade.get_all_data()

        assert result["exchange_name"] == "IB"
        assert result["exec_id"] == "000123"
        assert result["order_id"] == 456
        assert result["side"] == "BOT"
        assert result["shares"] == 100
        assert result["price"] == 150.0
