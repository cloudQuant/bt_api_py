"""
HitBTC Integration Tests

This module provides basic tests for HitBTC exchange integration.
Tests basic functionality without requiring API keys.
"""

from __future__ import annotations

import pytest

from bt_api_py.containers.balances.hitbtc_balance import HitBtcRequestBalanceData
from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcExchangeDataSpot
from bt_api_py.containers.orderbooks.hitbtc_orderbook import HitBtcRequestOrderBookData
from bt_api_py.containers.orders.hitbtc_order import HitBtcRequestOrderData
from bt_api_py.containers.tickers.hitbtc_ticker import HitBtcRequestTickerData
from bt_api_py.exchange_registers.register_hitbtc import register_hitbtc


class TestHitBtcFeedRegistration:
    """Test HitBTC feed registration"""

    def test_register_hitbtc(self):
        """Test that HitBTC feeds are registered correctly"""
        from bt_api_py.registry import ExchangeRegistry

        register_hitbtc()

        assert ExchangeRegistry.has_exchange("HITBTC___SPOT")


class TestHitBtcExchangeData:
    """Test HitBTC exchange data container"""

    def test_exchange_data_initialization(self):
        """Test exchange data initialization"""
        data = HitBtcExchangeDataSpot()

        assert data.exchange_name == "HITBTC___SPOT"
        assert data.asset_type == "SPOT"
        assert data.rest_url == "https://api.hitbtc.com/api/3"
        assert data.wss_url == "wss://api.hitbtc.com/api/3/ws/public"

    def test_get_rest_path(self):
        """Test REST path generation"""
        data = HitBtcExchangeDataSpot()

        path = data.get_rest_path("get_tick")
        assert "/public/ticker/" in path

        path = data.get_rest_path("make_order")
        assert "/spot/order" in path

        assert data.get_symbol("BTC/USDT") == "BTCUSDT"
        assert data.get_symbol("btcusdt") == "BTCUSDT"

    def test_get_period_conversion(self):
        """Test period conversion"""
        data = HitBtcExchangeDataSpot()

        assert data.get_period("1m") == "M1"
        assert data.get_period("5m") == "M5"
        assert data.get_period("1h") == "H1"
        assert data.get_period("1d") == "D1"


class TestHitBtcDataContainers:
    """Test HitBTC data containers"""

    @pytest.mark.ticker
    def test_ticker_data(self):
        """Test ticker data container"""
        ticker_info = {
            "symbol": "BTCUSDT",
            "last": "50000.0",
            "bid": "49900.0",
            "ask": "50100.0",
            "volume": "1.5",
            "high": "51000.0",
            "low": "48000.0",
            "open": "49000.0",
            "timestamp": 1640995200000,
            "count": 100,
        }

        ticker = HitBtcRequestTickerData(ticker_info, "BTC/USDT", "SPOT")
        ticker.init_data()

        assert ticker.symbol_name == "BTC/USDT"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49900.0
        assert ticker.ask_price == 50100.0
        assert ticker.high_price == 51000.0
        assert ticker.low_price == 48000.0

    @pytest.mark.orderbook
    def test_orderbook_data(self):
        """Test orderbook data container"""
        orderbook_info = {
            "symbol": "BTCUSDT",
            "timestamp": 1640995200000,
            "bid": [["49900.0", "0.5"], ["49800.0", "1.0"]],
            "ask": [["50100.0", "0.3"], ["50200.0", "0.8"]],
        }

        orderbook = HitBtcRequestOrderBookData(orderbook_info, "BTC/USDT", "SPOT")
        orderbook.init_data()

        assert orderbook.symbol_name == "BTC/USDT"
        assert len(orderbook.bids) == 2
        assert len(orderbook.asks) == 2
        assert orderbook.get_best_bid() == 49900.0
        assert orderbook.get_best_ask() == 50100.0

    def test_order_data(self):
        """Test order data container"""
        order_info = {
            "id": "123456",
            "client_order_id": "my_order_001",
            "symbol": "BTCUSDT",
            "side": "buy",
            "type": "limit",
            "status": "new",
            "quantity": "0.001",
            "quantity_filled": "0.0",
            "price": "50000.0",
            "price_filled": "0.0",
            "time_in_force": "GTC",
            "created_at": 1640995200000,
            "updated_at": 1640995200000,
        }

        order = HitBtcRequestOrderData(order_info, "BTC/USDT", "SPOT")
        order.init_data()

        assert order.symbol_name == "BTC/USDT"
        assert order.side == "buy"
        assert order.type == "limit"
        assert order.status == "new"
        assert order.quantity == 0.001
        assert order.get_filled_ratio() == 0.0
        assert order.is_active()

    def test_balance_data(self):
        """Test balance data container"""
        balance_info = {
            "currency": "BTC",
            "available": "0.5",
            "reserved": "0.1",
            "timestamp": 1640995200000,
        }

        balance = HitBtcRequestBalanceData(balance_info, None, "SPOT")
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 0.5
        assert balance.reserved == 0.1
        assert balance.get_total() == 0.6
        assert balance.get_free() == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
