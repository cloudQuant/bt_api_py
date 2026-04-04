"""Tests for BitbnsExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitbns_exchange_data import BitbnsExchangeData


class TestBitbnsExchangeData:
    """Tests for BitbnsExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitbnsExchangeData()

        assert exchange.exchange_name == "bitbns"
        assert "bitbns.com" in exchange.rest_url
