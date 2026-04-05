"""Tests for YobitExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.yobit_exchange_data import YobitExchangeData


class TestYobitExchangeData:
    """Tests for YobitExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = YobitExchangeData()

        assert exchange.exchange_name == "YOBIT"
