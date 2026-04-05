"""Tests for WazirxExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeData


class TestWazirxExchangeData:
    """Tests for WazirxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = WazirxExchangeData()

        assert exchange.exchange_name == "WAZIRX"
