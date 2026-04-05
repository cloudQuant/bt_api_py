"""Tests for HTX balance container."""

from __future__ import annotations

from bt_api_py.containers.balances.htx_balance import HtxRequestBalanceData


class TestHtxRequestBalanceData:
    """Tests for HtxRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = HtxRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "HTX"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "data": {
                "list": [
                    {"currency": "btc", "type": "trade", "balance": "1.0"},
                    {"currency": "btc", "type": "frozen", "balance": "0.5"},
                ]
            }
        }
        balance = HtxRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.available_margin == 1.0
        assert balance.used_margin == 0.5

    def test_init_data_default_usdt(self):
        """Test init_data defaults to USDT if symbol not found."""
        data = {
            "data": {
                "list": [
                    {"currency": "usdt", "type": "trade", "balance": "1000.0"},
                    {"currency": "usdt", "type": "frozen", "balance": "100.0"},
                ]
            }
        }
        balance = HtxRequestBalanceData(
            data, symbol_name=None, asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.available_margin == 1000.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = HtxRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        assert balance.get_exchange_name() == "HTX"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        balance = HtxRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        assert balance.get_symbol_name() == "BTC"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = HtxRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        assert balance.get_asset_type() == "SPOT"

    def test_get_available_margin(self):
        """Test get_available_margin."""
        data = {"data": {"list": [{"currency": "btc", "type": "trade", "balance": "1.0"}]}}
        balance = HtxRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )

        assert balance.get_available_margin() == 1.0

    def test_get_used_margin(self):
        """Test get_used_margin."""
        data = {"data": {"list": [{"currency": "btc", "type": "frozen", "balance": "0.5"}]}}
        balance = HtxRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )

        assert balance.get_used_margin() == 0.5

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"data": {"list": []}}
        balance = HtxRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"data": {"list": []}}
        balance = HtxRequestBalanceData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(balance)

        assert "HTX" in result
        assert "BTC" in result
