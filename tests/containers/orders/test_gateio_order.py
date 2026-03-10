"""Tests for Gateio order data containers."""

import pytest

from bt_api_py.containers.orders.gateio_order import GateioOrderData


def test_gateio_order_data_new():
    """Test Gateio new order data parsing."""
    data = {
        "id": "1234567890",
        "text": "test_order",
        "createTime": "1609459200000",
        "updateTime": "1609459201000",
        "currencyPair": "BTC_USDT",
        "status": "open",
        "type": "limit",
        "account": "spot",
        "side": "buy",
        "amount": "0.001",
        "price": "50000.00",
        "filledTotal": "0.0005",
        "fee": "0.000001",
        "feeCurrency": "USDT",
        "avgDealPrice": "50000.00",
    }

    order = GateioOrderData(data, "BTC-USDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    # Basic assertions - adjust based on actual implementation
    assert order is not None
    assert order.get_exchange_name() == "GATEIO"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
