"""Tests for HitBtcRequestOrderBookData container."""

from __future__ import annotations

from bt_api_py.containers.orderbooks.hitbtc_orderbook import HitBtcRequestOrderBookData


class TestHitBtcRequestOrderBookData:
    """Tests for HitBtcRequestOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = HitBtcRequestOrderBookData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert orderbook.exchange_name == "HITBTC"
        assert orderbook.symbol_name == "BTCUSD"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"bid": [["50000.0", "1.0"]], "ask": [["50010.0", "1.0"]]}
        orderbook = HitBtcRequestOrderBookData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = HitBtcRequestOrderBookData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "HITBTC"
        assert result["symbol_name"] == "BTCUSD"
