"""Tests for GiottusExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeData


class TestGiottusExchangeData:
    """Tests for GiottusExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = GiottusExchangeData()

        assert exchange.exchange_name == "giottus"
