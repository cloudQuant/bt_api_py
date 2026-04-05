"""Tests for OKX Open Interest container."""

import json

from bt_api_py.containers.openinterests import OkxOpenInterestData


class TestOkxOpenInterestData:
    """Tests for OkxOpenInterestData container."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "oi": "1000000",
            "oiCcy": "BTC",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        assert oi.event == "OpenInterestEvent"
        assert oi.exchange_name == "OKX"
        assert oi.symbol_name == "BTC-USDT-SWAP"
        assert oi.asset_type == "SWAP"

    def test_init_with_json_string(self):
        """Test initialization with JSON string."""
        data = {
            "ts": "1700000000000",
            "instId": "ETH-USDT-SWAP",
            "oi": "500000",
            "oiCcy": "ETH",
        }
        json_str = json.dumps(data)
        oi = OkxOpenInterestData(
            json_str, symbol_name="ETH-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=False
        )

        oi.init_data()

        assert oi.server_time == 1700000000000.0
        assert oi.open_interest_symbol_name == "ETH-USDT-SWAP"
        assert oi.open_interest == 500000.0
        assert oi.open_interest_ccy == "ETH"

    def test_init_data(self):
        """Test init_data method."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "oi": "1000000.5",
            "oiCcy": "BTC",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        result = oi.init_data()

        assert result is oi  # Returns self
        assert oi.has_been_init_data is True
        assert oi.server_time == 1700000000000.0
        assert oi.open_interest_symbol_name == "BTC-USDT-SWAP"
        assert oi.open_interest == 1000000.5
        assert oi.open_interest_ccy == "BTC"

    def test_init_data_idempotent(self):
        """Test that init_data is idempotent."""
        data = {"ts": "1700000000000", "instId": "BTC-USDT-SWAP", "oi": "1000000", "oiCcy": "BTC"}
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        oi.init_data()
        first_oi = oi.open_interest

        oi.init_data()
        assert oi.open_interest == first_oi

    def test_get_all_data(self):
        """Test get_all_data method."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "oi": "1000000",
            "oiCcy": "BTC",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        oi.init_data()

        all_data = oi.get_all_data()

        assert all_data["exchange_name"] == "OKX"
        assert all_data["symbol_name"] == "BTC-USDT-SWAP"
        assert all_data["asset_type"] == "SWAP"
        assert all_data["server_time"] == 1700000000000.0
        assert all_data["open_interest_symbol_name"] == "BTC-USDT-SWAP"
        assert all_data["open_interest"] == 1000000.0
        assert all_data["open_interest_ccy"] == "BTC"

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "ts": "1700000000000",
            "instId": "ETH-USDT-SWAP",
            "oi": "500000",
            "oiCcy": "ETH",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="ETH-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        oi.init_data()

        assert oi.get_event() == "OpenInterestEvent"
        assert oi.get_exchange_name() == "OKX"
        assert oi.get_symbol_name() == "ETH-USDT-SWAP"
        assert oi.get_asset_type() == "SWAP"
        assert oi.get_server_time() == 1700000000000.0
        assert oi.get_open_interest_symbol_name() == "ETH-USDT-SWAP"
        assert oi.get_open_interest() == 500000.0
        assert oi.get_open_interest_ccy() == "ETH"
        assert oi.get_local_update_time() is not None

    def test_str_representation(self):
        """Test __str__ and __repr__ methods."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "oi": "1000000",
            "oiCcy": "BTC",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )

        str_result = str(oi)
        repr_result = repr(oi)

        assert str_result == repr_result
        # Verify it's valid JSON
        parsed = json.loads(str_result)
        assert parsed["open_interest"] == 1000000.0

    def test_get_all_data_caching(self):
        """Test that get_all_data caches result."""
        data = {
            "ts": "1700000000000",
            "instId": "BTC-USDT-SWAP",
            "oi": "1000000",
            "oiCcy": "BTC",
        }
        oi = OkxOpenInterestData(
            data, symbol_name="BTC-USDT-SWAP", asset_type="SWAP", has_been_json_encoded=True
        )
        oi.init_data()

        all_data1 = oi.get_all_data()
        all_data2 = oi.get_all_data()

        assert all_data1 is all_data2  # Same object (cached)
