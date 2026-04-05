"""Tests for Kucoin order data containers."""

from __future__ import annotations

import pytest

from bt_api_py.containers.orders.kucoin_order import KuCoinRequestOrderData


def test_kucoin_order_data_new():
    """Test Kucoin new order data parsing."""
    data = {
        "code": "200000",
        "data": {
            "id": "1234567890",
            "symbol": "BTC-USDT",
            "opType": "DEAL",
            "type": "limit",
            "side": "buy",
            "price": "50000.00",
            "size": "0.001",
            "funds": "50.00",
            "dealSize": "0.0005",
            "dealFunds": "25.00",
            "fee": "0.00001",
            "feeCurrency": "USDT",
            "stp": None,
            "stopTriggered": False,
            "createdAt": 1609459200000,
            "isActive": True,
        },
    }

    order = KuCoinRequestOrderData(data, "BTC-USDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    # Basic assertions - adjust based on actual implementation
    assert order is not None
    assert order.get_exchange_name() == "KUCOIN"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
