"""Tests for Hyperliquid account container."""

import pytest

from bt_api_py.containers.accounts.hyperliquid_account import HyperliquidSpotWssAccountData


class TestHyperliquidSpotWssAccountData:
    """Tests for HyperliquidSpotWssAccountData."""

    def test_init(self):
        """Test initialization."""
        account = HyperliquidSpotWssAccountData({}, symbol_name="BTC", asset_type="SPOT")

        assert account.exchange_name == "HYPERLIQUID"
        assert account.symbol_name == "BTC"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False
        assert account.positions == []
        assert account.balances == []

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "user": "0x1234567890abcdef",
            "accountValue": 10000.0,
            "totalMarginUsed": 500.0,
            "initialMargin": 300.0,
            "assetPositions": [
                {"coin": "BTC", "szi": "1.0"}
            ],
            "balances": [
                {"coin": "USDC", "hold": "1000.0"}
            ]
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.user_address == "0x1234567890abcdef"
        assert account.account_value == 10000.0
        assert account.total_margin_used == 500.0
        assert account.initial_margin == 300.0
        assert len(account.positions) == 1
        assert len(account.balances) == 1

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "user": "0x1234567890abcdef",
            "accountValue": 10000.0,
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()
        first_value = account.account_value

        account.init_data()
        assert account.account_value == first_value

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = HyperliquidSpotWssAccountData({}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        assert account.get_exchange_name() == "HYPERLIQUID"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        account = HyperliquidSpotWssAccountData({}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        assert account.get_symbol_name() == "BTC"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = HyperliquidSpotWssAccountData({}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        assert account.get_asset_type() == "SPOT"

    def test_get_user_address(self):
        """Test get_user_address."""
        data = {"user": "0x1234567890abcdef"}
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_user_address() == "0x1234567890abcdef"

    def test_get_account_value(self):
        """Test get_account_value."""
        data = {"accountValue": 10000.0}
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_account_value() == 10000.0

    def test_get_total_margin_used(self):
        """Test get_total_margin_used."""
        data = {"totalMarginUsed": 500.0}
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_total_margin_used() == 500.0

    def test_get_initial_margin(self):
        """Test get_initial_margin."""
        data = {"initialMargin": 300.0}
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_initial_margin() == 300.0

    def test_get_positions(self):
        """Test get_positions."""
        data = {
            "assetPositions": [
                {"coin": "BTC", "szi": "1.0"}
            ]
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        positions = account.get_positions()
        assert len(positions) == 1

    def test_get_balances(self):
        """Test get_balances."""
        data = {
            "balances": [
                {"coin": "USDC", "hold": "1000.0"}
            ]
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        balances = account.get_balances()
        assert len(balances) == 1

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {
            "user": "0x1234567890abcdef",
            "accountValue": 10000.0,
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()
        result = account.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"
        assert result["user_address"] == "0x1234567890abcdef"
        assert result["account_value"] == 10000.0

    def test_str_representation(self):
        """Test __str__ method."""
        data = {
            "user": "0x1234567890abcdef",
            "accountValue": 10000.0,
        }
        account = HyperliquidSpotWssAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()
        result = str(account)

        assert "HyperliquidSpotWssAccountData" in result
        assert "0x1234567890abcdef" in result
