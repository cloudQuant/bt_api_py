"""Tests for CryptoComBalance container."""


from bt_api_py.containers.balances.cryptocom_balance import CryptoComBalance


class TestCryptoComBalance:
    """Tests for CryptoComBalance."""

    def test_init(self):
        """Test initialization."""
        balance = CryptoComBalance()

        assert balance.exchange_name == "CRYPTOCOM"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "instrument_name": "BTC",
            "total_balance": "1.5",
            "total_available": "1.0",
            "total_locked": "0.5",
        }
        balance = CryptoComBalance(balance_info=data, has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.total_balance == 1.5
        assert balance.available_balance == 1.0
        assert balance.frozen_balance == 0.5

    def test_from_api_response(self):
        """Test from_api_response class method."""
        data = {
            "instrument_name": "BTC",
            "total_balance": "1.5",
            "total_available": "1.0",
            "total_locked": "0.5",
        }
        balance = CryptoComBalance.from_api_response(data)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.total_balance == 1.5

    def test_to_dict(self):
        """Test to_dict."""
        data = {
            "instrument_name": "BTC",
            "total_balance": "1.5",
            "total_available": "1.0",
            "total_locked": "0.5",
        }
        balance = CryptoComBalance(balance_info=data, has_been_json_encoded=True)
        result = balance.to_dict()

        assert result["currency"] == "BTC"
        assert result["total_balance"] == 1.5
