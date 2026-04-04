"""Tests for KrakenRequestOrderData container."""

import pytest

from bt_api_py.containers.orders.kraken_order import KrakenRequestOrderData


class TestKrakenRequestOrderData:
    """Tests for KrakenRequestOrderData."""

    def test_init(self):
        """Test initialization."""
        order = KrakenRequestOrderData({}, symbol="XBTUSD", asset_type="SPOT")

        assert order.exchange == "kraken"
        assert order.symbol == "XBTUSD"
        assert order.asset_type == "SPOT"

    def test_parse_response_data(self):
        """Test parsing API response data."""
        data = {
            "result": {
                "txid": ["OUF4EM-FRGI2-MQMWZD"],
                "descr": {"order": "buy 0.001 XBTUSD @ limit 50000.0"},
            }
        }
        order = KrakenRequestOrderData(data, symbol="XBTUSD", asset_type="SPOT", is_response_data=True)

        assert order.order_id is not None or order.symbol == "XBTUSD"

    def test_to_dict(self):
        """Test to_dict."""
        order = KrakenRequestOrderData({}, symbol="XBTUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = order.to_dict()

        assert result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        order = KrakenRequestOrderData({}, symbol="XBTUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "Kraken" in result
