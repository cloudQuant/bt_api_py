"""Tests for HtxExchangeData container."""

from __future__ import annotations

import json

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeData


class TestHtxExchangeData:
    """Tests for HtxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = HtxExchangeData()

        assert exchange.exchange_name == "htx"

    def test_symbol_and_period_conversions(self):
        exchange = HtxExchangeData()

        assert exchange.get_symbol("BTC/USDT") == "btcusdt"
        assert exchange.get_symbol("BTC-USDT") == "btcusdt"
        assert exchange.get_period("1m") == "1min"
        assert exchange.get_standard_period("1min") == "1m"

    def test_get_rest_path(self):
        exchange = HtxExchangeData()
        exchange.rest_paths = {"get_tick": "/market/detail/merged"}

        assert exchange.get_rest_path("get_tick") == "/market/detail/merged"
        assert exchange.get_rest_path("missing") == ""

    def test_get_wss_path_builds_subscription_message(self):
        exchange = HtxExchangeData()
        exchange.wss_paths = {
            "depth": {"params": ["{symbol}.depth.{type}"], "method": "SUBSCRIBE", "id": 1}
        }

        payload = json.loads(exchange.get_wss_path(topic="depth", symbol="BTC/USDT", type="step0"))

        assert payload["sub"] == "market.btcusdt.depth.step0"
        assert payload["id"] == "depth_BTC/USDT"

    def test_get_wss_path_returns_empty_payload_for_unknown_topic(self):
        exchange = HtxExchangeData()
        assert exchange.get_wss_path(topic="unknown") == "{}"
