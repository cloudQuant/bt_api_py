"""Tests for HtxExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeData


class TestHtxExchangeData:
    """Tests for HtxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = HtxExchangeData()

        assert exchange.exchange_name == "htx"
