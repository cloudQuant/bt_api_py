"""Tests for Bybit balance containers."""

from bt_api_py.containers.balances.bybit_balance import (
    BybitBalanceData,
    BybitSpotBalanceData,
    BybitSwapBalanceData,
)


class TestBybitBalanceData:
    """Tests for BybitBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = BybitBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "BYBIT"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "result": {
                "list": [
                    {
                        "accountType": "UNIFIED",
                        "totalEquity": "10000.0",
                        "totalWalletBalance": "9000.0",
                        "totalAvailableBalance": "8000.0",
                        "coin": [
                            {
                                "coin": "USDT",
                                "walletBalance": "5000.0",
                                "availableToWithdraw": "4000.0",
                                "locked": "1000.0",
                                "equity": "5000.0",
                                "unrealisedPnl": "0.0",
                            }
                        ],
                    }
                ]
            }
        }
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.account_type == "UNIFIED"
        assert balance.total_equity == "10000.0"
        assert len(balance.coins) == 1

    def test_get_coin_balance(self):
        """Test get_coin_balance."""
        data = {
            "result": {
                "list": [
                    {
                        "coin": [
                            {
                                "coin": "USDT",
                                "walletBalance": "5000.0",
                                "availableToWithdraw": "4000.0",
                                "locked": "1000.0",
                                "equity": "5000.0",
                                "unrealisedPnl": "0.0",
                            }
                        ]
                    }
                ]
            }
        }
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()
        coin_balance = balance.get_coin_balance("USDT")

        assert coin_balance is not None
        assert coin_balance["coin"] == "USDT"

    def test_get_coin_balance_not_found(self):
        """Test get_coin_balance returns None for missing coin."""
        data = {"result": {"list": [{"coin": []}]}}
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()
        coin_balance = balance.get_coin_balance("ETH")

        assert coin_balance is None

    def test_get_available_balance(self):
        """Test get_available_balance."""
        data = {
            "result": {
                "list": [
                    {
                        "coin": [
                            {
                                "coin": "USDT",
                                "walletBalance": "5000.0",
                                "availableToWithdraw": "4000.0",
                                "locked": "1000.0",
                                "equity": "5000.0",
                                "unrealisedPnl": "0.0",
                            }
                        ]
                    }
                ]
            }
        }
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.get_available_balance("USDT") == 4000.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = BybitBalanceData({}, asset_type="SPOT")
        assert balance.get_exchange_name() == "BYBIT"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = BybitBalanceData({}, asset_type="SPOT")
        assert balance.get_asset_type() == "SPOT"

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"result": {"list": [{"accountType": "UNIFIED", "coin": []}]}}
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "BYBIT"
        assert result["asset_type"] == "SPOT"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"result": {"list": [{"accountType": "UNIFIED", "coin": []}]}}
        balance = BybitBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = str(balance)

        assert "BybitBalance" in result
        assert "UNIFIED" in result


class TestBybitSpotBalanceData:
    """Tests for BybitSpotBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = BybitSpotBalanceData({})

        assert balance.asset_type == "spot"
        assert balance.exchange_name == "BYBIT"


class TestBybitSwapBalanceData:
    """Tests for BybitSwapBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = BybitSwapBalanceData({})

        assert balance.asset_type == "swap"
        assert balance.exchange_name == "BYBIT"
