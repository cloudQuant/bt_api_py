"""Tests for OKX asset module."""

import pytest

from bt_api_py.containers.assets.okx_asset import OkxCurrencyData


class TestOkxCurrencyData:
    """Tests for OkxCurrencyData class."""

    def test_init(self):
        """Test initialization."""
        currency = OkxCurrencyData({"ccy": "BTC"}, has_been_json_encoded=True)

        assert currency.currency_info == {"ccy": "BTC"}
        assert currency.has_been_json_encoded is True
        assert currency.currency is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        currency = OkxCurrencyData('{"ccy": "BTC"}', has_been_json_encoded=False)

        assert currency.has_been_json_encoded is False
        assert currency.currency_info is None

    def test_init_data(self):
        """Test init_data method."""
        currency_info = {
            "ccy": "BTC",
            "name": "Bitcoin",
            "chain": "BTC",
            "minWd": "0.001",
            "wdFee": "0.0001",
            "canWd": "1",
            "canDep": "1",
        }
        currency = OkxCurrencyData(currency_info, has_been_json_encoded=True)
        currency.init_data()

        assert currency.currency == "BTC"
        assert currency.name == "Bitcoin"
        assert currency.chain == "BTC"
        assert currency.min_withdrawal_amt == 0.001
        assert currency.withdrawal_fee == 0.0001
        assert currency.can_withdraw is True
        assert currency.can_deposit is True

    def test_get_currency(self):
        """Test get_currency method."""
        currency = OkxCurrencyData({"ccy": "BTC"}, has_been_json_encoded=True)
        currency.init_data()

        assert currency.get_currency() == "BTC"

    def test_get_name(self):
        """Test get_name method."""
        currency = OkxCurrencyData({"name": "Bitcoin"}, has_been_json_encoded=True)
        currency.init_data()

        assert currency.get_name() == "Bitcoin"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
