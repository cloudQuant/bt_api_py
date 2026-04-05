"""Tests for Kraken balance container."""

import pytest

from bt_api_py.containers.balances.kraken_balance import (
    KrakenRequestBalanceData,
    KrakenSpotWssBalanceData,
)


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

    def test_validate_and_summary_helpers(self):
        data = {"result": {"XXBT": "1.5", "ZUSD": "1000.0", "ETH": "2.0"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance._initialized = True

        assert balance.validate() is True
        assert balance.get_fiat_balance("USD") > 0
        assert balance.get_crypto_balance() > 0
        assert "XXBT" in balance.get_stakable_balance()
        biggest = balance.get_biggest_holding()
        assert biggest is not None
        assert biggest[0] in {"XXBT", "ZUSD", "ETH"}

    def test_update_balance_updates_totals(self):
        data = {"result": {"XXBT": "1.0"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance._initialized = True
        original_total = balance.total_value_usd

        balance.update_balance("XXBT", 0.5)

        assert balance.get_currency_balance("XXBT") == 1.5
        assert balance.total_value_usd > original_total

    def test_update_balance_rejects_negative_result(self):
        data = {"result": {"XXBT": "1.0"}}
        balance = KrakenRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance._initialized = True

        balance.update_balance("XXBT", -2.0)

        assert balance.get_currency_balance("XXBT") == 1.0

    def test_invalid_payload_raises_value_error(self):
        with pytest.raises(ValueError):
            KrakenRequestBalanceData({"result": {}}, asset_type="SPOT", has_been_json_encoded=True)


class TestKrakenSpotWssBalanceData:
    def test_structured_wss_payload(self):
        balance = KrakenSpotWssBalanceData(
            {"currency": "XXBT", "free": "0.5", "used": "0.2", "time": 123456.0},
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        data = balance.to_dict()
        assert data["currency"] == "XXBT"
        assert data["total"] == 0.7
        assert data["exchange"] == "kraken"

    def test_dict_payload_uses_first_non_zero_currency(self):
        balance = KrakenSpotWssBalanceData(
            {"ZUSD": "0", "XXBT": "0.5", "ETH": "1.0"},
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        data = balance.to_dict()
        assert data["currency"] == "XXBT"
        assert data["free"] == 0.5

    def test_non_dict_payload_falls_back_to_unknown(self):
        balance = KrakenSpotWssBalanceData([], asset_type="SPOT", has_been_json_encoded=True)
        assert balance.to_dict()["currency"] == "UNKNOWN"
