"""Tests for Hyperliquid balance containers."""

from __future__ import annotations

from bt_api_py.containers.balances.hyperliquid_balance import (
    HyperliquidSpotRequestBalanceData,
    HyperliquidSwapRequestBalanceData,
)


class TestHyperliquidSwapRequestBalanceData:
    """Tests for HyperliquidSwapRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = HyperliquidSwapRequestBalanceData({}, symbol_name="BTC", asset_type="SWAP")

        assert balance.exchange_name == "HYPERLIQUID"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SWAP"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "assetPositions": [
                {
                    "coin": "BTC",
                    "position": {
                        "coin": "BTC",
                        "sz": "1.0",
                        "free collateral": "0.5",
                        "margin held": "0.1",
                        "unrealizedPnl": "100.0",
                    },
                }
            ],
            "marginSummary": {
                "accountValue": "10000.0",
                "totalMarginUsed": "100.0",
                "initialMargin": "50.0",
            },
        }
        balance = HyperliquidSwapRequestBalanceData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.coin == "BTC"
        assert balance.total == 1.0
        assert balance.available == 0.5

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = HyperliquidSwapRequestBalanceData({}, symbol_name="BTC", asset_type="SWAP")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_exchange_name() == "HYPERLIQUID"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        balance = HyperliquidSwapRequestBalanceData({}, symbol_name="BTC", asset_type="SWAP")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_symbol_name() == "BTC"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = HyperliquidSwapRequestBalanceData({}, symbol_name="BTC", asset_type="SWAP")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_asset_type() == "SWAP"

    def test_get_all_data(self):
        """Test get_all_data."""
        balance = HyperliquidSwapRequestBalanceData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"
        assert result["symbol_name"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        balance = HyperliquidSwapRequestBalanceData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(balance)

        assert "HyperliquidSwapRequestBalanceData" in result


class TestHyperliquidSpotRequestBalanceData:
    """Tests for HyperliquidSpotRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = HyperliquidSpotRequestBalanceData({}, symbol_name="USDC", asset_type="SPOT")

        assert balance.exchange_name == "HYPERLIQUID"
        assert balance.symbol_name == "USDC"
        assert balance.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "balances": [
                {
                    "coin": "USDC",
                    "total": "1000.0",
                    "free": "900.0",
                    "hold": "100.0",
                }
            ]
        }
        balance = HyperliquidSpotRequestBalanceData(
            data, symbol_name="USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.coin == "USDC"
        assert balance.total == 1000.0
        assert balance.available == 900.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = HyperliquidSpotRequestBalanceData({}, symbol_name="USDC", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_exchange_name() == "HYPERLIQUID"

    def test_get_all_data(self):
        """Test get_all_data."""
        balance = HyperliquidSpotRequestBalanceData(
            {}, symbol_name="USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = balance.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"

    def test_str_representation(self):
        """Test __str__ method."""
        balance = HyperliquidSpotRequestBalanceData(
            {}, symbol_name="USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(balance)

        assert "HyperliquidSpotRequestBalanceData" in result
