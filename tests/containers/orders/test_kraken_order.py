"""Tests for KrakenRequestOrderData container."""


from bt_api_py.containers.orders.kraken_order import KrakenRequestOrderData, KrakenSpotWssOrderData


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
        data = {"txid": "OUF4EM-FRGI2-MQMWZD"}
        order = KrakenRequestOrderData(
            data, symbol="XBTUSD", asset_type="SPOT", is_response_data=True
        )

        assert order.order_id == "OUF4EM-FRGI2-MQMWZD"
        assert order.status == "new"
        assert order.get_exchange_name() == "kraken"

    def test_to_dict(self):
        """Test to_dict."""
        order = KrakenRequestOrderData(
            {}, symbol="XBTUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.to_dict()

        assert result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        order = KrakenRequestOrderData(
            {}, symbol="XBTUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "Kraken" in result

    def test_normalized_data_helpers_and_lifecycle(self):
        order = KrakenRequestOrderData(
            {
                "id": "OID-1",
                "symbol": "XBTUSD",
                "side": "buy",
                "type": "limit",
                "created_at": 1000,
                "quantity": "2.0",
                "price": "50000.0",
                "executed_quantity": "0.5",
                "cost": "25000.0",
                "fee": "10.0",
                "userref": 7,
            },
            symbol="XBTUSD",
            asset_type="SPOT",
        )

        order.status = "open"

        assert order.get_order_id() == "OID-1"
        assert order.get_symbol_name() == "XBTUSD"
        assert order.get_asset_type() == "SPOT"
        assert order.get_order_status() == "open"
        assert order.get_order_side() == "buy"
        assert order.get_order_type() == "limit"
        assert order.get_order_size() == 2.0
        assert order.get_order_price() == 50000.0
        assert order.get_executed_qty() == 0.5
        assert order.get_cum_quote() == 25000.0
        assert order.get_client_order_id() == 7
        assert order.get_order_exchange_id() == "kraken"
        assert order.validate() is True
        assert order.is_open() is True
        assert order.is_filled() is False
        assert order.get_fill_percentage() == 25.0

        order.update_from_trade({"executed_quantity": 2.0, "cost": 100000.0, "fee": 20.0})
        assert order.is_filled() is True
        assert order.status == "closed"

        order.cancel()
        assert order.status == "canceled"
        assert order.remaining_quantity == 0

    def test_wss_order_to_dict(self):
        order = KrakenSpotWssOrderData(
            {
                "orderId": "OID-2",
                "symbol": "XBTUSD",
                "status": "open",
                "side": "sell",
                "type": "limit",
                "qty": "1.5",
                "price": "51000.0",
                "executedQty": "0.5",
                "remainingQty": "1.0",
                "time": 2000,
            },
            symbol="XBTUSD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        result = order.to_dict()

        assert result["order_id"] == "OID-2"
        assert result["symbol"] == "XBTUSD"
        assert result["status"] == "open"
        assert result["side"] == "sell"
        assert result["quantity"] == 1.5
        assert result["executed_quantity"] == 0.5
        assert result["remaining_quantity"] == 1.0
