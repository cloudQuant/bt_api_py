"""Tests for CTP order module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.ctp.ctp_order import CTP_DIRECTION_MAP, CTP_ORDER_STATUS_MAP, CtpOrderData


class TestCtpOrderData:
    """Tests for CtpOrderData class."""

    def test_init(self):
        """Test initialization."""
        order = CtpOrderData(
            {"OrderSysID": "12345"},
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert order.exchange_name == "CTP"
        assert order.symbol_name == "IF2506"
        assert order.asset_type == "FUTURE"

    def test_ctp_order_status_map(self):
        """Test CTP order status map."""
        assert CTP_ORDER_STATUS_MAP["0"] is not None
        assert CTP_ORDER_STATUS_MAP["1"] is not None
        assert CTP_ORDER_STATUS_MAP["5"] is not None

    def test_ctp_direction_map(self):
        """Test CTP direction map."""
        assert CTP_DIRECTION_MAP["0"] == "buy"
        assert CTP_DIRECTION_MAP["1"] == "sell"

    def test_order_data_inheritance(self):
        """Test that CtpOrderData inherits from OrderData."""
        order = CtpOrderData({}, has_been_json_encoded=True)

        assert hasattr(order, "order_info")
        assert hasattr(order, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
