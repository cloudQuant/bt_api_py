"""Tests for OKX Price Limit container."""

import json
import pytest

from bt_api_py.containers.pricelimits import OkxPriceLimitData


class TestOkxPriceLimitData:
    """Tests for OkxPriceLimitData container."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)

        assert pl.event == "PriceLimitEvent"
        assert pl.exchange_name == "OKX"
        assert pl.symbol_name == "BTC-USDT-SWAP"
        assert pl.asset_type == "SWAP"

    def test_init_with_json_string(self):
        """Test initialization with JSON string."""
        data = {
            "ts": "1700000000000",
            "instId": "ETH-USDT-SWAP",
            "buyLmt": "3000",
            "sellLmt": "2900",
        }
        json_str = json.dumps(data)
        pl = OkxPriceLimitData(json_str, symbol_name="ETH-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=False)

        pl.init_data()

        assert pl.server_time == 1700000000000.0
        assert pl.price_limit_symbol_name == "ETH-USDT-SWAP"
        assert pl.buy_limit == 3000.0
        assert pl.sell_limit == 2900.0

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000.5",
            "sellLmt": "49000.5",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)

        result = pl.init_data()

        assert result is pl  # Returns self
        assert pl.has_been_init_data is True
        assert pl.server_time == 1700000000000.0
        assert pl.price_limit_symbol_name == "BTC-USDT-SWAP"
        assert pl.buy_limit == 50000.5
        assert pl.sell_limit == 49000.5

    def test_init_data_idempotent(self):
        """Test that init_data is idempotent."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)

        pl.init_data()
        first_buy = pl.buy_limit

        pl.init_data()
        assert pl.buy_limit == first_buy

    def test_get_all_data(self):
        """Test get_all_data method."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)
        pl.init_data()

        all_data = pl.get_all_data()

        assert all_data["exchange_name"] == "OKX"
        assert all_data["symbol_name"] == "BTC-USDT-SWAP"
        assert all_data["asset_type"] == "SWAP"
        assert all_data["server_time"] == 1700000000000.0
        assert all_data["price_limit_symbol_name"] == "BTC-USDT-SWAP"
        assert all_data["buy_limit"] == 50000.0
        assert all_data["sell_limit"] == 49000.0

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ts": "1700000000000",
            "instId": "ETH-USDT-SWAP",
            "buyLmt": "3000",
            "sellLmt": "2900",
        }
        pl = OkxPriceLimitData(data, symbol_name="ETH-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)
        pl.init_data()

        assert pl.get_event() == "PriceLimitEvent"
        assert pl.get_exchange_name() == "OKX"
        assert pl.get_symbol_name() == "ETH-USDT-SWAP"
        assert pl.get_asset_type() == "SWAP"
        assert pl.get_server_time() == 1700000000000.0
        assert pl.get_price_limit_symbol_name() == "ETH-USDT-SWAP"
        assert pl.get_buy_limit() == 3000.0
        assert pl.get_sell_limit() == 2900.0
        assert pl.get_local_update_time() is not None

    def test_str_representation(self):
        """Test __str__ and __repr__ methods."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)

        str_result = str(pl)
        repr_result = repr(pl)

        assert str_result == repr_result
        # Verify it's valid JSON
        parsed = json.loads(str_result)
        assert parsed["buy_limit"] == 50000.0
        assert parsed["sell_limit"] == 49000.0

    def test_get_all_data_caching(self):
        """Test that get_all_data caches result."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True)
        pl.init_data()

        all_data1 = pl.get_all_data()
        all_data2 = pl.get_all_data()

        assert all_data1 is all_data2  # Same object (cached)

    def test_init_with_dict_direct(self):
        """Test initialization with dict directly (not JSON encoded)."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "49000",
        }
        # Test the branch where price_limit_info is already a dict but not json_encoded
        pl = OkxPriceLimitData(data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=False)

        pl.init_data()

        assert pl.buy_limit == 50000.0
        assert pl.sell_limit == 49000.0
