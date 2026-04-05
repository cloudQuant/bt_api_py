"""Tests for trade module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.trades.trade import TradeData


class TestTradeData:
    """Tests for TradeData class."""

    def test_init(self):
        """Test initialization."""
        trade = TradeData({"trade_id": "12345"}, has_been_json_encoded=True)

        assert trade.event == "TradeEvent"
        assert trade.trade_info == {"trade_id": "12345"}
        assert trade.has_been_json_encoded is True
        assert trade.exchange_name is None
        assert trade.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        trade = TradeData('{"trade_id": "12345"}', has_been_json_encoded=False)

        assert trade.event == "TradeEvent"
        assert trade.trade_info == '{"trade_id": "12345"}'
        assert trade.has_been_json_encoded is False
        assert trade.trade_data is None

    def test_init_with_symbol_and_asset_type(self):
        """Test initialization with symbol and asset type."""
        trade = TradeData(
            {"trade_id": "12345"},
            has_been_json_encoded=True,
            symbol_name="BTCUSDT",
            asset_type="SPOT",
        )

        assert trade.symbol_name == "BTCUSDT"
        assert trade.asset_type == "SPOT"

    def test_get_event(self):
        """Test get_event method."""
        trade = TradeData({}, has_been_json_encoded=True)

        assert trade.get_event() == "TradeEvent"

    def test_default_values(self):
        """Test default values."""
        trade = TradeData({}, has_been_json_encoded=True)

        assert trade.trade_id is None
        assert trade.trade_price is None
        assert trade.trade_volume is None
        assert trade.trade_side is None
        assert trade.trade_fee is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
