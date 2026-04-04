"""Tests for BigONEExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bigone_exchange_data import BigONEExchangeData


class TestBigONEExchangeData:
    """Tests for BigONEExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BigONEExchangeData()

        assert exchange.exchange_name == "BIGONE___SPOT"
        assert "big.one" in exchange.rest_url
