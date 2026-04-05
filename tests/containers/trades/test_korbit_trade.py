"""Tests for KorbitSpotWssTradeData container."""

from __future__ import annotations

from bt_api_py.containers.trades.korbit_trade import KorbitSpotWssTradeData


class TestKorbitSpotWssTradeData:
    """Tests for KorbitSpotWssTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = KorbitSpotWssTradeData({}, symbol_name="BTC_KRW", asset_type="SPOT")

        assert trade.exchange_name == "KORBIT"
        assert trade.symbol_name == "BTC_KRW"
        assert trade.asset_type == "SPOT"
