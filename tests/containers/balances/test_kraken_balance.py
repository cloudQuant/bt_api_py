"""Tests for Kraken balance container."""

import pytest

from bt_api_py.containers.balances.kraken_balance import KrakenRequestBalanceData


class TestKrakenRequestBalanceData:
    """Tests for KrakenRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)

        assert balance.exchange == "kraken"
        assert balance.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "result": {
                "XXBT": "1.5000000000",
                "ZEUR": "1000.0000",
            }
        }
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)

        assert "XXBT" in balance.balances
        assert balance.balances["XXBT"]["total"] == 1.5

    def test_exchange_name(self):
        """Test exchange name."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        # KrakenRequestBalanceData stores exchange as "kraken"
        assert balance.exchange == "kraken"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        # KrakenRequestBalanceData stores asset_type directly
        assert balance.asset_type == "SPOT"

    def test_to_dict(self):
        """Test to_dict."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = balance.to_dict()

        assert result["exchange"] == "kraken"
        assert "balances" in result

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        result = str(balance)

        assert "KrakenBalance" in result

    def test_get_currency_balance(self):
        """Test get_currency_balance."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True

        assert balance.get_currency_balance("XXBT") == 1.5

    def test_get_currency_balance_not_found(self):
        """Test get_currency_balance returns None for missing currency."""
        data = {"result": {"XXBT": "1.5"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True

        assert balance.get_currency_balance("ETH") is None
