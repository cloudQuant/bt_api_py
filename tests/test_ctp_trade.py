"""Tests for CTP trade module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.ctp.ctp_trade import CtpTradeData


class TestCtpTradeData:
    """Tests for CtpTradeData class."""

    def test_init(self):
        """Test initialization."""
        trade = CtpTradeData(
            {"TradeID": "12345"},
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert trade.exchange_name == "CTP"
        assert trade.symbol_name == "IF2506"
        assert trade.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        trade_info = {
            "InstrumentID": "IF2506",
            "TradeID": "12345",
            "OrderRef": "000001",
            "OrderSysID": "999",
            "Direction": "0",
            "OffsetFlag": "0",
            "Price": 4000.0,
            "Volume": 1,
            "TradeDate": "20250101",
            "TradeTime": "09:30:00",
            "ExchangeID": "CFFEX",
        }
        trade = CtpTradeData(
            trade_info, symbol_name="IF2506", asset_type="FUTURE", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.instrument_id == "IF2506"
        assert trade.trade_id_value == "12345"
        assert trade.order_ref == "000001"
        assert trade.price == 4000.0
        assert trade.volume == 1
        assert trade.exchange_id == "CFFEX"

    def test_trade_data_inheritance(self):
        """Test that CtpTradeData inherits from TradeData."""
        trade = CtpTradeData({}, symbol_name="IF2506", has_been_json_encoded=True)

        assert hasattr(trade, "trade_info")
        assert hasattr(trade, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
