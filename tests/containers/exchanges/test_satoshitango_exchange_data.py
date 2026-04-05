"""Tests for SatoshitangoExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.satoshitango_exchange_data import SatoshiTangoExchangeData


class TestSatoshiTangoExchangeData:
    """Tests for SatoshiTangoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = SatoshiTangoExchangeData()

        assert exchange.exchange_name == "satoshitango"
