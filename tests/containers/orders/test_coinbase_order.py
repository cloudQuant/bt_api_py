"""Tests for CoinbaseOrderData container."""

import json

from bt_api_py.containers.orders.coinbase_order import (
    CoinbaseOrderData,
    CoinbaseRequestOrderData,
    CoinbaseWssOrderData,
)


class TestCoinbaseOrderData:
    """Tests for CoinbaseOrderData."""

    def test_init(self):
        """Test initialization."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert order.exchange_name == "COINBASE"
        assert order.symbol_name == "BTC-USD"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "order_id": "123456",
            "client_order_id": "abc123",
            "product_id": "BTC-USD",
            "side": "buy",
            "status": "filled",
            "price": "50000.0",
            "size": "1.0",
            "filled_size": "1.0",
        }
        order = CoinbaseOrderData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "buy"
        assert order.price == 50000.0

    def test_init_data_parses_fields_and_getters(self):
        data = {
            "order_id": "123456",
            "client_order_id": "abc123",
            "product_id": "BTC-USD",
            "side": "buy",
            "status": "filled",
            "price": "50000.0",
            "size": "1.0",
            "filled_size": "1.0",
            "remaining_size": "0.0",
            "funds": "50000.0",
            "filled_funds": "50000.0",
            "remaining_funds": "0.0",
            "created_time": "2024-01-01T00:00:00Z",
            "done_time": "2024-01-01T00:01:00Z",
            "done_reason": "filled",
            "settled": "true",
        }
        order = CoinbaseOrderData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.get_order_id() == "123456"
        assert order.get_client_order_id() == "abc123"
        assert order.get_product_id() == "BTC-USD"
        assert order.get_side() == "buy"
        assert order.get_status() == "filled"
        assert order.get_price() == 50000.0
        assert order.get_size() == 1.0
        assert order.get_filled_size() == 1.0
        assert order.get_remaining_size() == 0.0
        assert order.get_funds() == 50000.0
        assert order.get_filled_funds() == 50000.0
        assert order.get_remaining_funds() == 0.0
        assert order.get_created_time() == "2024-01-01T00:00:00Z"
        assert order.get_done_time() == "2024-01-01T00:01:00Z"
        assert order.get_done_reason() == "filled"
        assert order.get_settled() == "true"
        assert order.get_order_type() == "limit"

    def test_get_all_data(self):
        """Test get_all_data."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "COINBASE" in result

    def test_request_and_wss_subclasses_parse_payloads(self):
        request = CoinbaseRequestOrderData(
            json.dumps(
                {
                    "order_id": "r1",
                    "client_order_id": "client-r1",
                    "product_id": "BTC-USD",
                    "side": "sell",
                    "status": "open",
                    "price": "50010.0",
                    "size": "0.5",
                    "filled_size": "0.1",
                    "remaining_size": "0.4",
                    "funds": "25005.0",
                    "filled_funds": "5001.0",
                    "remaining_funds": "20004.0",
                    "settled": "false"
                }
            ),
            symbol_name="BTC-USD",
            asset_type="SPOT",
        )
        request.init_data()

        wss = CoinbaseWssOrderData(
            {
                "order_id": "w1",
                "client_order_id": "client-w1",
                "product_id": "BTC-USD",
                "side": "buy",
                "status": "pending",
                "type": "market",
                "size": "0.25",
                "filled_size": "0.0",
                "remaining_size": "0.25",
                "settled": "false",
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_order_id() == "r1"
        assert request.get_order_type() == "limit"
        assert request.get_side() == "sell"
        assert wss.get_order_id() == "w1"
        assert wss.get_order_type() == "market"
        assert wss.get_status() == "pending"
        assert "COINBASE" in repr(wss)
