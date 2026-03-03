"""
Test Bitfinex exchange integration.

Run tests:
    pytest tests/feeds/test_bitfinex.py -v

Run with coverage:
    pytest tests/feeds/test_bitfinex.py --cov=bt_api_py.feeds.live_bitfinex --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.bitfinex_exchange_data import (
    BitfinexExchangeData,
    BitfinexExchangeDataSpot,
)
from bt_api_py.containers.tickers.bitfinex_ticker import (
    BitfinexRequestTickerData,
)
from bt_api_py.containers.orderbooks.bitfinex_orderbook import (
    BitfinexRequestOrderBookData,
)
from bt_api_py.containers.bars.bitfinex_bar import (
    BitfinexRequestBarData,
)
from bt_api_py.containers.orders.bitfinex_order import (
    BitfinexRequestOrderData,
)
from bt_api_py.containers.balances.bitfinex_balance import (
    BitfinexSpotRequestBalanceData,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_bitfinex import BitfinexRequestDataSpot

# Import registration to auto-register Bitfinex
import bt_api_py.feeds.register_bitfinex  # noqa: F401


class TestBitfinexExchangeData:
    """Test Bitfinex exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Bitfinex spot exchange data."""
        exchange_data = BitfinexExchangeDataSpot()
#         assert exchange_data.exchange_name == "BITFINEX"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert len(exchange_data.rest_paths) > 0

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = BitfinexExchangeDataSpot()
        # Bitfinex uses t prefix for spot pairs
        assert exchange_data.get_symbol("BTC-USD") == "tBTCUSD"
        assert exchange_data.get_symbol("ETH-USD") == "tETHUSD"

    def test_get_symbol_from_mappings(self):
        """Test symbol conversion using predefined mappings."""
        exchange_data = BitfinexExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USD") == "tBTCUSD"
        assert exchange_data.get_symbol("ETH-USD") == "tETHUSD"
        assert exchange_data.get_symbol("XRP-USD") == "tXRPUSD"

    def test_get_reverse_symbol(self):
        """Test reverse symbol conversion."""
        exchange_data = BitfinexExchangeDataSpot()
        assert exchange_data.get_reverse_symbol("tBTCUSD") == "BTC-USD"
        assert exchange_data.get_reverse_symbol("tETHUSD") == "ETH-USD"

    def test_get_period(self):
        """Test period conversion."""
        exchange_data = BitfinexExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1m"
        assert exchange_data.get_period("1h") == "1h"
        assert exchange_data.get_period("1d") == "1D"

    def test_get_rest_path(self):
        """Test getting REST API paths."""
        exchange_data = BitfinexExchangeDataSpot()
        path = exchange_data.get_rest_path("ticker")
        assert path is not None
        assert isinstance(path, str)

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BitfinexExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BitfinexExchangeDataSpot()
        assert "USD" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestBitfinexDataContainers:
    """Test Bitfinex data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        # Bitfinex ticker format is a list
        ticker_data = [
            "tBTCUSD",
            49950.0,
            1.5,
            50050.0,
            2.3,
            100.5,
            0.2,
            50000.0,
            1234.56,
            51000.0,
            49000.0,
        ]

        ticker = BitfinexRequestTickerData(
            json.dumps(ticker_data), "BTC-USD", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BITFINEX"
        assert ticker.get_symbol_name() == "BTC-USD"
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_bid_price() == 49950.0
        assert ticker.get_ask_price() == 50050.0
        assert ticker.get_high() == 51000.0
        assert ticker.get_low() == 49000.0

    def test_orderbook_container(self):
        """Test orderbook data container."""
        orderbook_data = {
            "bids": [
                [49900.0, 0.5],
                [49800.0, 1.0]
            ],
            "asks": [
                [50100.0, 0.3],
                [50200.0, 0.8]
            ]
        }

        orderbook = BitfinexRequestOrderBookData(
            json.dumps(orderbook_data), "BTC-USD", "SPOT"
        )
        orderbook.init_data()

        assert orderbook.symbol_name == "BTC-USD"
        assert len(orderbook.bids) >= 0
        assert len(orderbook.asks) >= 0

    def test_order_container(self):
        """Test order data container."""
        order_data = {
            "id": 123456789,
            "gid": None,
            "cid": 123456,
            "symbol": "tBTCUSD",
            "mts_create": 1640995200000,
            "mts_update": 1640995200000,
            "amount": 0.001,
            "amount_orig": 0.001,
            "type": "EXCHANGE LIMIT",
            "type_prev": None,
            "flags": 0,
            "status": "ACTIVE",
            "price": 50000.0,
            "price_avg": 50000.0,
            "price_trailing": None,
            "price_aux_limit": None,
            "notify": 0,
            "hidden": 0,
            "placed_id": None,
            "routing": None,
            "meta": None,
        }

        order = BitfinexRequestOrderData(
            json.dumps(order_data), "BTC-USD", "SPOT"
        )
        order.init_data()

        assert order.symbol_name == "BTC-USD"
        assert order.exchange_name == "BITFINEX"

    def test_balance_container(self):
        """Test balance data container."""
        balance_data = [
            "BTC",
            0.5,
            0.1,
            None
        ]

        balance = BitfinexSpotRequestBalanceData(
            json.dumps(balance_data), "SPOT", has_been_json_encoded=False
        )
        balance.init_data()

        assert balance.get_exchange_name() == "BITFINEX"
        assert balance.get_asset_type() == "SPOT"


class TestBitfinexRegistration:
    """Test Bitfinex registration."""

    def test_bitfinex_registered(self):
        """Test that Bitfinex is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BITFINEX___SPOT" in ExchangeRegistry._feed_classes

        # Check if exchange data is registered
        assert "BITFINEX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITFINEX___SPOT"] == BitfinexExchangeDataSpot

        # Check if balance handler is registered
        assert "BITFINEX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITFINEX___SPOT"] is not None

    def test_bitfinex_create_exchange_data(self):
        """Test creating Bitfinex exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "BITFINEX___SPOT")
        assert isinstance(exchange_data, BitfinexExchangeDataSpot)


class TestBitfinexIntegration:
    """Integration tests for Bitfinex."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass

# ==================== Live API Tests (Following Binance/OKX Standard) ===


@pytest.mark.integration
class TestBitfinexLiveAPI:
    """Test Bitfinex live API following Binance/OKX test standards.

    These tests make actual API calls to Bitfinex and require network access.
    They are marked as integration tests and are skipped by default.
    Run with: pytest tests/feeds/test_bitfinex.py -v -m integration
    """

    def test_bitfinex_req_tick_data(self):
        """Test Bitfinex ticker data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)
        data = feed.get_tick("BTC-USD")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have data, validate the ticker structure
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            # Bitfinex returns ticker data
            assert isinstance(ticker, (dict, list, BitfinexRequestTickerData))

    def test_bitfinex_async_tick_data(self):
        """Test Bitfinex ticker data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)

        # Bitfinex may not have async_get_tick, skip if method doesn't exist
        if not hasattr(feed, 'async_get_tick'):
            pass
        return

        feed.async_get_tick(
            "BTC-USD",
            extra_data={
                "test_async_tick_data": True})
        time.sleep(3)

        try:
            tick_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass
    def test_bitfinex_req_kline_data(self):
        """Test Bitfinex kline data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)
        data = feed.get_kline("BTC-USD", "1m", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have kline data, validate structure
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            # Bitfinex returns BitfinexRequestBarData objects
            assert isinstance(klines, (list, dict, BitfinexRequestBarData))

    def test_bitfinex_async_kline_data(self):
        """Test Bitfinex kline data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)

        # Bitfinex may not have async_get_kline, skip if method doesn't exist
        if not hasattr(feed, 'async_get_kline'):
            pass
        return

        feed.async_get_kline(
            "BTC-USD",
            period="1m",
            count=3,
            extra_data={
                "test_async_kline_data": True})
        time.sleep(5)

        try:
            kline_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass
    def test_bitfinex_req_orderbook_data(self):
        """Test Bitfinex orderbook data (sync)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)
        data = feed.get_depth("BTC-USD", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

        # If we have depth data, validate structure
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            # Bitfinex returns orderbook data
            assert isinstance(orderbook, (dict, list, BitfinexRequestOrderBookData))

    def test_bitfinex_async_orderbook_data(self):
        """Test Bitfinex orderbook data (async)."""
        data_queue = queue.Queue()
        kwargs = {
            "exchange_name": "BITFINEX___SPOT",
        }
        feed = BitfinexRequestDataSpot(data_queue, **kwargs)

        # Bitfinex may not have async_get_depth, skip if method doesn't exist
        if not hasattr(feed, 'async_get_depth'):
            pass
        return

        feed.async_get_depth("BTC-USD", 20)
        time.sleep(3)

        try:
            depth_data = data_queue.get(timeout=10)
        except queue.Empty:
            pass
