"""Tests for CtpExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeData


class TestCtpExchangeData:
    """Tests for CtpExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CtpExchangeData()

        assert exchange.exchange_name == "CTP"
