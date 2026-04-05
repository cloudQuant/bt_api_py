"""Tests for MexcOrderData container."""

import json

from bt_api_py.containers.orders.mexc_order import (
    MexcOrderData,
    MexcRequestOrderData,
    MexcWssOrderData,
)


class TestMexcOrderData:
    """Tests for MexcOrderData."""

    def test_init(self):
        """Test initialization."""
        order = MexcOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert order.exchange_name == "MEXC"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "orderId": "123456",
            "clientOrderId": "abc123",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "side": "BUY",
            "type": "LIMIT",
            "origQty": "1.0",
            "executedQty": "1.0",
            "price": "50000.0",
        }
        order = MexcOrderData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id == 123456
        assert order.side == "BUY"
        assert order.price == 50000.0

    def test_init_data_parses_fields_and_helpers(self):
        data = {
            "orderId": "123456",
            "clientOrderId": "abc123",
            "symbol": "BTCUSDT",
            "status": "PARTIALLY_FILLED",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "origQty": "2.0",
            "executedQty": "0.5",
            "cummulativeQuoteQty": "25000.0",
            "price": "50000.0",
            "stopPrice": "0.0",
            "icebergQty": "0.0",
            "time": 111,
            "updateTime": 222,
            "isWorking": True,
        }
        order = MexcOrderData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )

        assert order.get_order_id() == 123456
        assert order.get_client_order_id() == "abc123"
        assert order.get_status() == "PARTIALLY_FILLED"
        assert order.get_side() == "BUY"
        assert order.get_type() == "LIMIT"
        assert order.get_time_in_force() == "GTC"
        assert order.get_quantity() == 2.0
        assert order.get_executed_qty() == 0.5
        assert order.get_cummulative_quote_qty() == 25000.0
        assert order.get_price() == 50000.0
        assert order.get_time() == 111
        assert order.get_update_time() == 222
        assert order.get_is_working() is True
        assert order.is_open() is True
        assert order.is_closed() is False
        assert order.is_filled() is False
        assert order.is_canceled() is False
        assert order.get_filled_percentage() == 25.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = MexcOrderData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        order = MexcOrderData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "MEXC" in result

    def test_request_and_wss_subclasses_parse_payloads(self):
        request = MexcRequestOrderData(
            json.dumps(
                {
                    "orderId": "1",
                    "clientOrderId": "client-1",
                    "status": "FILLED",
                    "side": "SELL",
                    "type": "MARKET",
                    "timeInForce": "IOC",
                    "origQty": "1.0",
                    "executedQty": "1.0",
                    "cummulativeQuoteQty": "100.0",
                    "price": "100.0",
                    "time": 10,
                    "updateTime": 11,
                }
            ),
            symbol_name="BTCUSDT",
            asset_type="SPOT",
        )
        request.init_data()

        wss = MexcWssOrderData(
            {
                "E": 20,
                "data": {
                    "i": "2",
                    "c": "client-2",
                    "s": "NEW",
                    "S": "BUY",
                    "o": "LIMIT",
                    "q": "3.0",
                    "z": "0.0",
                    "p": "200.0",
                },
            },
            symbol_name="BTCUSDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_order_id() == 1
        assert request.is_filled() is True
        assert wss.get_order_id() == 2
        assert wss.get_status() == "NEW"
        assert wss.get_side() == "BUY"
        assert wss.get_type() == "LIMIT"
        assert wss.get_quantity() == 3.0
        assert wss.get_price() == 200.0
        assert wss.get_time() == 20
        assert "MEXC" in repr(wss)
