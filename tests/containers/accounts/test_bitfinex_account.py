"""Tests for Bitfinex account container."""

from bt_api_py.containers.accounts.bitfinex_account import (
    BitfinexSpotRequestAccountData,
    BitfinexSpotWssAccountData,
)


class TestBitfinexSpotRequestAccountData:
    """Tests for BitfinexSpotRequestAccountData."""

    def test_init(self):
        """Test initialization."""
        account = BitfinexSpotRequestAccountData({}, asset_type="SPOT")

        assert account.exchange_name == "BITFINEX"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "id": "12345",
            "type": "trading",
            "currency": "USD",
            "balance": 10000.0,
            "available": 8000.0,
            "timestamp": "1712217600",
        }
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        account.init_data()

        assert account.account_id == "12345"
        assert account.account_type == "trading"
        assert account.currency == "USD"
        assert account.balance == 10000.0
        assert account.available == 8000.0
        assert account.timestamp == "1712217600"

    def test_init_data_with_json_string(self):
        """Test init_data with JSON string."""
        import json

        data = json.dumps({"id": "12345", "currency": "USD"})
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        account.init_data()

        assert account.account_id == "12345"
        assert account.currency == "USD"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"id": "12345"}
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        account.init_data()
        first_id = account.account_id

        account.init_data()
        assert account.account_id == first_id

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "id": "12345",
            "currency": "USD",
            "balance": 10000.0,
        }
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        result = account.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["account_id"] == "12345"
        assert result["currency"] == "USD"
        assert result["balance"] == 10000.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"id": "12345", "currency": "USD"}
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        result = str(account)

        assert "BITFINEX" in result
        assert "12345" in result

    def test_repr_representation(self):
        """Test __repr__ method."""
        data = {"id": "12345"}
        account = BitfinexSpotRequestAccountData(data, asset_type="SPOT")
        assert repr(account) == str(account)


class TestBitfinexSpotWssAccountData:
    """Tests for BitfinexSpotWssAccountData."""

    def test_inherits_from_request(self):
        """Test that WssAccountData inherits from RequestAccountData."""
        data = {"id": "12345"}
        account = BitfinexSpotWssAccountData(data, asset_type="SPOT")

        assert account.exchange_name == "BITFINEX"
        assert account.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data works for WSS account."""
        data = {
            "id": "67890",
            "currency": "BTC",
            "balance": 1.5,
        }
        account = BitfinexSpotWssAccountData(data, asset_type="SPOT")
        account.init_data()

        assert account.account_id == "67890"
        assert account.currency == "BTC"
        assert account.balance == 1.5
