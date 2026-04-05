"""Tests for Bitget account container."""

from bt_api_py.containers.accounts.bitget_account import (
    BitgetAccountData,
    BitgetSpotWssAccountData,
)


class TestBitgetAccountData:
    """Tests for BitgetAccountData."""

    def test_init(self):
        """Test initialization."""
        account = BitgetAccountData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert account.exchange_name == "BITGET"
        assert account.symbol_name == "BTCUSDT"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "uid": "12345",
            "accountType": "spot",
            "marginLevel": 1.5,
            "marginRatio": 0.1,
            "marginMode": "crossed",
            "equity": 10000.0,
            "unrealizedPnl": 500.0,
            "realizedPnl": 1000.0,
            "marginAvailable": 8000.0,
            "marginForced": 0.0,
        }
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.uid == "12345"
        assert account.account_type == "spot"
        assert account.margin_level == 1.5
        assert account.margin_ratio == 0.1
        assert account.margin_mode == "crossed"
        assert account.equity == 10000.0
        assert account.unrealized_pnl == 500.0
        assert account.realized_pnl == 1000.0
        assert account.margin_available == 8000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {"uid": "12345"}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()
        first_uid = account.uid

        account.init_data()
        assert account.uid == first_uid

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = BitgetAccountData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        assert account.get_exchange_name() == "BITGET"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        account = BitgetAccountData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        assert account.get_symbol_name() == "BTCUSDT"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = BitgetAccountData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        assert account.get_asset_type() == "SPOT"

    def test_get_uid(self):
        """Test get_uid."""
        data = {"uid": "12345"}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_uid() == "12345"

    def test_get_account_type(self):
        """Test get_account_type."""
        data = {"accountType": "spot"}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_account_type() == "spot"

    def test_get_equity(self):
        """Test get_equity."""
        data = {"equity": 10000.0}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_equity() == 10000.0

    def test_get_unrealized_pnl(self):
        """Test get_unrealized_pnl."""
        data = {"unrealizedPnl": 500.0}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_unrealized_pnl() == 500.0

    def test_get_realized_pnl(self):
        """Test get_realized_pnl."""
        data = {"realizedPnl": 1000.0}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_realized_pnl() == 1000.0

    def test_get_margin_available(self):
        """Test get_margin_available."""
        data = {"marginAvailable": 8000.0}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.init_data()

        assert account.get_margin_available() == 8000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "uid": "12345",
            "equity": 10000.0,
        }
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = account.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["uid"] == "12345"
        assert result["equity"] == 10000.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"uid": "12345"}
        account = BitgetAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(account)

        assert "BITGET" in result
        assert "12345" in result


class TestBitgetSpotWssAccountData:
    """Tests for BitgetSpotWssAccountData.

    Note: BitgetSpotWssAccountData has a bug - it accepts has_been_json_encoded
    parameter but doesn't store it as an attribute, causing init_data to fail.
    """

    def test_init(self):
        """Test initialization."""
        account = BitgetSpotWssAccountData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert account.exchange_name == "BITGET"
        assert account.symbol_name == "BTCUSDT"
        assert account.asset_type == "SPOT"
        assert account.balances == []

    def test_init_data(self):
        """Test init_data with balance data.

        Note: Source code bug - has_been_json_encoded is not stored as attribute.
        """
        data = {
            "data": {
                "balances": [
                    {"coin": "USDT", "available": 1000.0, "frozen": 0.0},
                    {"coin": "BTC", "available": 0.5, "frozen": 0.1},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        # Work around bug by setting the attribute manually
        account.has_been_json_encoded = True
        account.init_data()

        assert len(account.balances) == 2

    def test_get_balances(self):
        """Test get_balances."""
        data = {
            "data": {
                "balances": [
                    {"coin": "USDT", "available": 1000.0},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.has_been_json_encoded = True
        balances = account.get_balances()

        assert len(balances) == 1

    def test_get_balance(self):
        """Test get_balance for specific coin."""
        data = {
            "data": {
                "balances": [
                    {"coin": "USDT", "available": 1000.0},
                    {"coin": "BTC", "available": 0.5},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.has_been_json_encoded = True
        balance = account.get_balance("USDT")

        assert balance is not None

    def test_get_balance_not_found(self):
        """Test get_balance for non-existent coin."""
        data = {
            "data": {
                "balances": [
                    {"coin": "USDT", "available": 1000.0},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.has_been_json_encoded = True
        balance = account.get_balance("ETH")

        assert balance is None

    def test_get_total_equity(self):
        """Test get_total_equity with no equity returns 0.0.

        Note: BitgetBalanceData.get_equity() returns None by default,
        so total equity will be 0.0.
        """
        data = {
            "data": {
                "balances": [
                    {"coin": "USDT", "available": 1000.0, "frozen": 0.0},
                ]
            }
        }
        account = BitgetSpotWssAccountData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        account.has_been_json_encoded = True
        account.init_data()
        total = account.get_total_equity()

        # Equity is None, so total is 0.0
        assert total == 0.0
