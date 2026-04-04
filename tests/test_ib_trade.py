"""Tests for IB trade module."""

import pytest

from bt_api_py.containers.ib.ib_trade import IbTradeData


class TestIbTradeData:
    """Tests for IbTradeData class."""

    def test_init(self):
        """Test initialization."""
        trade = IbTradeData(
            {"execId": "12345"},
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )

        assert trade.exchange_name == "IB"
        assert trade.symbol_name == "AAPL"
        assert trade.asset_type == "STK"

    def test_init_data(self):
        """Test init_data method."""
        trade_info = {
            "execId": "12345",
            "orderId": 999,
            "permId": 888,
            "side": "BOT",
            "shares": 100,
            "price": 150.0,
            "cumQty": 100,
            "avgPrice": 150.5,
            "time": "2025-01-01 09:30:00",
            "commission": 1.0,
            "exchange": "SMART",
        }
        trade = IbTradeData(
            trade_info,
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.exec_id == "12345"
        assert trade.order_id_val == 999
        assert trade.perm_id == 888
        assert trade.side == "BOT"
        assert trade.shares == 100.0
        assert trade.price_val == 150.0
        assert trade.cum_qty == 100.0
        assert trade.avg_price_val == 150.5
        assert trade.exec_time == "2025-01-01 09:30:00"
        assert trade.commission_val == 1.0
        assert trade.exchange_val == "SMART"

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        trade = IbTradeData({}, has_been_json_encoded=True)

        assert trade.get_exchange_name() == "IB"

    def test_trade_data_inheritance(self):
        """Test that IbTradeData inherits from TradeData."""
        trade = IbTradeData({}, has_been_json_encoded=True)

        assert hasattr(trade, "trade_info")
        assert hasattr(trade, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
