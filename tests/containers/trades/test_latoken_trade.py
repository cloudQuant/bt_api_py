"""Tests for LatokenSpotWssTradeData container."""

import pytest

from bt_api_py.containers.trades.latoken_trade import LatokenSpotWssTradeData


class TestLatokenSpotWssTradeData:
    """Tests for LatokenSpotWssTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = LatokenSpotWssTradeData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert trade.exchange_name == "LATOKEN"
        assert trade.symbol_name == "BTC_USDT"
        assert trade.asset_type == "SPOT"
