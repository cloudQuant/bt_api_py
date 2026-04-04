"""Tests for OKX currency data module."""

import pytest

from bt_api_py.containers.assets.okx_asset import OkxCurrencyData


class TestOkxCurrencyData:
    """Tests for OkxCurrencyData class."""

    def test_init(self):
        """Test initialization."""
        currency = OkxCurrencyData(
            {"ccy": "BTC"},
            has_been_json_encoded=True
        )

        assert currency.has_been_json_encoded is True

    def test_init_data(self):
        """Test init_data method."""
        currency_info = {
            "ccy": "BTC",
            "name": "Bitcoin",
            "chain": "BTC-Bitcoin",
            "minWd": "0.001",
            "wdFee": "0.0005",
            "canWd": "1",
            "canDep": "1",
            "canInternal": "1",
        }
        currency = OkxCurrencyData(
            currency_info,
            has_been_json_encoded=True
        )
        currency.init_data()

        assert currency.currency == "BTC"
        assert currency.name == "Bitcoin"
        assert currency.chain == "BTC-Bitcoin"
        assert currency.min_withdrawal_amt == 0.001
        assert currency.withdrawal_fee == 0.0005
        assert currency.can_withdraw is True
        assert currency.can_deposit is True
        assert currency.can_internal is True

    def test_get_currency(self):
        """Test get_currency method."""
        currency = OkxCurrencyData(
            {"ccy": "BTC"},
            has_been_json_encoded=True
        )
        currency.init_data()

        assert currency.get_currency() == "BTC"

    def test_get_name(self):
        """Test get_name method."""
        currency = OkxCurrencyData(
            {"name": "Bitcoin"},
            has_been_json_encoded=True
        )
        currency.init_data()

        assert currency.get_name() == "Bitcoin"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
