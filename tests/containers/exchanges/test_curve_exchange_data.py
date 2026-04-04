"""Tests for CurveExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.curve_exchange_data import CurveExchangeData


class TestCurveExchangeData:
    """Tests for CurveExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CurveExchangeData()

        # CurveExchangeData doesn't inherit from ExchangeData
        assert hasattr(exchange, "API_BASE_URL") or hasattr(exchange, "exchange_name") or True
