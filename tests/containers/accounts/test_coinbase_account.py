"""Tests for CoinbaseAccountData container."""


from bt_api_py.containers.accounts.coinbase_account import (
    CoinbaseAccountData,
    CoinbaseRequestAccountData,
    CoinbaseSpotWssAccountData,
)


class TestCoinbaseAccountData:
    """Tests for CoinbaseAccountData."""

    def test_init(self):
        """Test initialization."""
        account = CoinbaseAccountData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert account.exchange_name == "COINBASE"
        assert account.symbol_name == "BTC-USD"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "uuid": "test-account",
            "currency": "BTC",
            "available_balance": {"value": "1.0"},
            "hold": {"value": "0.5"},
            "updated_at": "2024-01-01T00:00:00Z",
        }
        account = CoinbaseAccountData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.has_been_init_data is True
        assert account.get_account_id() == "test-account"
        assert account.get_currency() == "BTC"
        assert account.get_available() == 1.0
        assert account.get_hold() == 0.5
        assert account.get_balance() == 1.5
        assert account.get_last_activity() == "2024-01-01T00:00:00Z"

    def test_get_all_data(self):
        """Test get_all_data method."""
        account = CoinbaseAccountData(
            {
                "uuid": "test-account",
                "currency": "BTC",
                "available_balance": {"value": "1.0"},
                "hold": {"value": "0.5"},
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        result = account.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"
        assert result["balance"] == 1.5

    def test_request_account_parses_account_wrapper(self):
        account = CoinbaseRequestAccountData(
            {
                "account": {
                    "uuid": "wrapped-account",
                    "currency": "ETH",
                    "available_balance": {"value": "2.0"},
                    "hold": {"value": "0.25"},
                    "updated_at": "2024-01-02T00:00:00Z",
                }
            },
            symbol_name="ETH-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        account.init_data()

        assert account.get_account_id() == "wrapped-account"
        assert account.get_currency() == "ETH"
        assert account.get_balance() == 2.25

    def test_request_account_selects_matching_currency_from_accounts(self):
        account = CoinbaseRequestAccountData(
            {
                "accounts": [
                    {
                        "uuid": "usd-account",
                        "currency": "USD",
                        "available_balance": {"value": "100"},
                        "hold": {"value": "0"},
                    },
                    {
                        "uuid": "btc-account",
                        "currency": "BTC",
                        "available_balance": {"value": "1.2"},
                        "hold": {"value": "0.3"},
                    },
                ]
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        account.init_data()

        assert account.get_account_id() == "btc-account"
        assert account.get_balance() == 1.5

    def test_wss_account_selects_matching_currency_from_accounts(self):
        account = CoinbaseSpotWssAccountData(
            {
                "accounts": [
                    {
                        "uuid": "eth-account",
                        "currency": "ETH",
                        "available_balance": {"value": "3.0"},
                        "hold": {"value": "0.5"},
                        "updated_at": "2024-01-03T00:00:00Z",
                    },
                    {
                        "uuid": "btc-account",
                        "currency": "BTC",
                        "available_balance": {"value": "0.4"},
                        "hold": {"value": "0.1"},
                        "updated_at": "2024-01-03T00:00:00Z",
                    },
                ]
            },
            symbol_name="ETH-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        account.init_data()

        assert account.get_account_id() == "eth-account"
        assert account.get_currency() == "ETH"
        assert account.get_balance() == 3.5
