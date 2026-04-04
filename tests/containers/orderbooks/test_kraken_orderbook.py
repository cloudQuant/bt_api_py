"""Tests for KrakenRequestOrderBookData container."""

from bt_api_py.containers.orderbooks.kraken_orderbook import KrakenRequestOrderBookData


class TestKrakenRequestOrderBookData:
    """Tests for KrakenRequestOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = KrakenRequestOrderBookData({}, symbol="XXBTZUSD", asset_type="SPOT")

        assert orderbook.symbol == "XXBTZUSD"
        assert orderbook.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data - data parsed in constructor."""
        data = {
            "result": {
                "XXBTZUSD": {
                    "bids": [["50000.0", "1.0", 1234567890]],
                    "asks": [["50010.0", "1.0", 1234567890]],
                }
            }
        }
        orderbook = KrakenRequestOrderBookData(
            data, symbol="XXBTZUSD", asset_type="SPOT", has_been_json_encoded=True
        )

        # Kraken parses data in constructor, init_data is no-op
        result = orderbook.init_data()
        assert result == orderbook

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        data = {"result": {"XXBTZUSD": {"bids": [], "asks": []}}}
        orderbook = KrakenRequestOrderBookData(
            data, symbol="XXBTZUSD", asset_type="SPOT", has_been_json_encoded=True
        )

        # Exchange name is set during parsing
        assert orderbook.get_exchange_name() == "kraken"
